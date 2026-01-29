"""Cleanup helpers for Autopilot temporary data."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CleanupResult:
    """Aggregate cleanup results."""

    removed: list[Path] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def add_removed(self, paths: Iterable[Path]) -> None:
        for path in paths:
            self.removed.append(path)

    def add_error(self, message: str) -> None:
        self.errors.append(message)


@dataclass
class ProcessCleanupResult:
    """Process cleanup results."""

    candidates: list[tuple[int, str]] = field(default_factory=list)
    terminated: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def add_error(self, message: str) -> None:
        self.errors.append(message)


def _safe_rmtree(path: Path, result: CleanupResult, dry_run: bool) -> None:
    if dry_run:
        result.add_removed([path])
        return
    try:
        shutil.rmtree(path)
        result.add_removed([path])
    except FileNotFoundError:
        return
    except Exception as exc:
        result.add_error(f"Failed to remove {path}: {exc}")


def find_codex_temp_dirs() -> list[Path]:
    """Find legacy codex temp directories created by mkdtemp(prefix='codex-nomcp-')."""
    paths: set[Path] = set()
    temp_dir = Path(tempfile.gettempdir())
    paths.update([p for p in temp_dir.glob("codex-nomcp-*") if p.is_dir()])

    var_folders = Path("/var/folders")
    if var_folders.exists():
        for p in var_folders.glob("*/*/T/codex-nomcp-*"):
            if p.is_dir():
                paths.add(p)

    return sorted(paths)


def cleanup_codex_temp(dry_run: bool = False) -> CleanupResult:
    """Remove legacy codex temp directories."""
    result = CleanupResult()
    for path in find_codex_temp_dirs():
        _safe_rmtree(path, result, dry_run=dry_run)
    return result


def _get_active_worktrees(repo_root: Path) -> set[Path]:
    active: set[Path] = set()
    try:
        proc = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception:
        return active

    for line in proc.stdout.splitlines():
        if line.startswith("worktree "):
            worktree_path = line.split(" ", 1)[1].strip()
            if worktree_path:
                active.add(Path(worktree_path).resolve())
    return active


def cleanup_worktrees(repo_root: Path, dry_run: bool = False) -> CleanupResult:
    """Remove inactive worktrees under .autopilot/worktrees."""
    result = CleanupResult()
    worktrees_root = repo_root / ".autopilot" / "worktrees"
    if not worktrees_root.exists():
        return result

    active = _get_active_worktrees(repo_root)

    for path in worktrees_root.iterdir():
        if not path.is_dir():
            continue
        if path.resolve() in active:
            continue
        _safe_rmtree(path, result, dry_run=dry_run)

    # Prune git metadata for stale worktrees (best-effort).
    if not dry_run:
        try:
            subprocess.run(
                ["git", "worktree", "prune"],
                cwd=repo_root,
                check=False,
                capture_output=True,
                text=True,
            )
        except Exception:
            pass

    return result


def cleanup_logs(repo_root: Path, dry_run: bool = False) -> CleanupResult:
    """Remove Autopilot log directory."""
    result = CleanupResult()
    log_dir = repo_root / ".autopilot" / "logs"
    if log_dir.exists():
        _safe_rmtree(log_dir, result, dry_run=dry_run)
    return result


def cleanup_codex_home(repo_root: Path, dry_run: bool = False) -> CleanupResult:
    """Remove the isolated codex home directory."""
    result = CleanupResult()
    codex_home = os.environ.get("AUTOPILOT_CODEX_HOME")
    if codex_home:
        path = Path(codex_home).expanduser()
    else:
        path = repo_root / ".autopilot" / "codex-home"
    if path.exists():
        _safe_rmtree(path, result, dry_run=dry_run)
    return result


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def find_codex_processes() -> list[tuple[int, str]]:
    """Find running Codex CLI processes."""
    try:
        proc = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception:
        return []

    candidates: list[tuple[int, str]] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pid_str, cmd = line.split(" ", 1)
            pid = int(pid_str)
        except ValueError:
            continue

        if "codex" not in cmd:
            continue
        # Match codex CLI invocations (avoid accidental matches on unrelated text).
        if " codex " in f" {cmd} " or cmd.endswith("/codex") or cmd.endswith("/codex "):
            candidates.append((pid, cmd))
        elif cmd.lstrip().startswith("codex "):
            candidates.append((pid, cmd))

    return candidates


def cleanup_codex_processes(
    dry_run: bool = False, timeout_sec: float = 2.0
) -> ProcessCleanupResult:
    """Terminate running Codex CLI processes."""
    result = ProcessCleanupResult()
    candidates = find_codex_processes()
    result.candidates = candidates

    if dry_run or not candidates:
        if dry_run:
            result.terminated = [pid for pid, _ in candidates]
        return result

    pids = [pid for pid, _ in candidates]
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        except Exception as exc:
            result.add_error(f"Failed to SIGTERM {pid}: {exc}")

    time.sleep(timeout_sec)

    still_alive = [pid for pid in pids if _pid_exists(pid)]
    for pid in still_alive:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            continue
        except Exception as exc:
            result.add_error(f"Failed to SIGKILL {pid}: {exc}")

    result.terminated = [pid for pid in pids if not _pid_exists(pid)]
    return result
