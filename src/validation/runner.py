"""Validation runner for format, lint, tests, and UAT."""

import logging
import os
import re
import shlex
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from ..utils.subprocess import SubprocessManager

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result of validation execution."""

    command_type: str  # format, lint, tests, uat
    success: bool
    exit_code: int | None
    output: str
    timed_out: bool = False
    stuck: bool = False


@dataclass
class UATResult:
    """Result of UAT execution."""

    success: bool
    exit_code: int | None
    output: str
    uat_file: Path | None
    timed_out: bool = False
    stuck: bool = False


class ValidationRunner:
    """Run validation commands."""

    def __init__(
        self,
        work_dir: Path,
        timeout_sec: int = 120,
        log_dir: Path | None = None,
        allow_no_tests: bool = True,
        venv_dir: Path | None = None,
    ):
        """Initialize validation runner.

        Args:
            work_dir: Working directory for commands
            timeout_sec: Default timeout for commands
            log_dir: Directory for command logs
        """
        self.work_dir = work_dir
        self.timeout_sec = timeout_sec
        self.log_dir = log_dir
        self.allow_no_tests = allow_no_tests
        # Shared per-repo virtualenv (keeps project deps out of Autopilot's global env).
        self.venv_dir = venv_dir or (work_dir / ".autopilot" / "venv")
        self._venv_python: Path | None = None
        self._auto_installed: set[str] = set()

    async def run_format(
        self,
        command: str | None,
        timeout_sec: int | None = None,
    ) -> ValidationResult:
        """Run format command.

        Args:
            command: Format command (optional)
            timeout_sec: Override timeout

        Returns:
            ValidationResult
        """
        if not command:
            logger.info("No format command configured, skipping")
            return ValidationResult(
                command_type="format",
                success=True,
                exit_code=None,
                output="Skipped (no command configured)",
            )

        logger.info(f"Running format: {command}")
        return await self._run_command(
            command_type="format",
            command=command,
            timeout_sec=timeout_sec or self.timeout_sec,
        )

    async def run_lint(
        self,
        command: str | None,
        timeout_sec: int | None = None,
    ) -> ValidationResult:
        """Run lint command.

        Args:
            command: Lint command (optional)
            timeout_sec: Override timeout

        Returns:
            ValidationResult
        """
        if not command:
            logger.info("No lint command configured, skipping")
            return ValidationResult(
                command_type="lint",
                success=True,
                exit_code=None,
                output="Skipped (no command configured)",
            )

        logger.info(f"Running lint: {command}")
        return await self._run_command(
            command_type="lint",
            command=command,
            timeout_sec=timeout_sec or self.timeout_sec,
        )

    async def run_tests(
        self,
        command: str,
        timeout_sec: int | None = None,
    ) -> ValidationResult:
        """Run test command.

        Args:
            command: Test command (required)
            timeout_sec: Override timeout

        Returns:
            ValidationResult

        Raises:
            ValueError: If command is None
        """
        if not command:
            raise ValueError("Tests command is required")

        logger.info(f"Running tests: {command}")
        return await self._run_command(
            command_type="tests",
            command=command,
            timeout_sec=timeout_sec or self.timeout_sec,
        )

    async def run_uat(
        self,
        command: str | None,
        timeout_sec: int | None = None,
    ) -> UATResult:
        """Run UAT command.

        Args:
            command: UAT command (optional)
            timeout_sec: Override timeout

        Returns:
            UATResult
        """
        if not command:
            logger.info("No UAT command configured, skipping")
            return UATResult(
                success=True,
                exit_code=None,
                output="Skipped (no command configured)",
                uat_file=None,
            )

        logger.info(f"Running UAT: {command}")
        result = await self._run_command(
            command_type="uat",
            command=command,
            timeout_sec=timeout_sec or self.timeout_sec,
        )

        return UATResult(
            success=result.success,
            exit_code=result.exit_code,
            output=result.output,
            uat_file=None,
            timed_out=result.timed_out,
            stuck=result.stuck,
        )

    async def run_all(
        self,
        commands: dict[str, str | None],
        timeout_sec: int | None = None,
    ) -> dict[str, ValidationResult]:
        """Run all validation commands in order.

        Args:
            commands: Dict of command_type -> command string
            timeout_sec: Override timeout for all commands

        Returns:
            Dict of command_type -> ValidationResult
        """
        results = {}

        # Run format first (optional)
        if "format" in commands:
            results["format"] = await self.run_format(
                command=commands.get("format"),
                timeout_sec=timeout_sec,
            )

        # Run lint second (optional)
        if "lint" in commands:
            results["lint"] = await self.run_lint(
                command=commands.get("lint"),
                timeout_sec=timeout_sec,
            )

        # Run tests last (required)
        if "tests" in commands:
            results["tests"] = await self.run_tests(
                command=commands["tests"],
                timeout_sec=timeout_sec,
            )

        return results

    async def _run_command(
        self,
        command_type: str,
        command: str,
        timeout_sec: int,
    ) -> ValidationResult:
        """Run a single command.

        Args:
            command_type: Type of command (for logging)
            command: Command string to execute
            timeout_sec: Timeout in seconds

        Returns:
            ValidationResult
        """
        venv_python: Path | None = None
        env: dict[str, str] | None = None
        if command_type in {"format", "lint", "tests", "uat"} and self._should_use_repo_venv(
            command_type=command_type,
            command=command,
        ):
            venv_python = await self._ensure_repo_venv()
            if venv_python:
                env = self._env_for_venv(venv_python)
                if command_type in {"tests", "uat"}:
                    command = self._rewrite_module_entrypoint(
                        command=command,
                        module="pytest",
                        python_executable=str(venv_python),
                    )
                if command_type in {"format", "lint"}:
                    command = self._rewrite_module_entrypoint(
                        command=command,
                        module="ruff",
                        python_executable=str(venv_python),
                    )

        if command_type == "lint" and self._command_invokes_tool(command, tool="ruff"):
            normalized = self._normalize_ruff_lint_command(command)
            if normalized != command:
                logger.info("Normalized ruff lint command: %s -> %s", command, normalized)
                command = normalized

        # Validation commands are user-configured strings and often rely on shell features
        # (e.g. `&&`, pipes, env vars, `source`/`.`). Run them through a shell for robustness.
        if os.name == "nt":
            args = ["cmd", "/c", command]
        else:
            # Use bash for compatibility with common dev workflows (venv activation, etc).
            args = ["bash", "-lc", command]

        # Run command
        manager = SubprocessManager(
            timeout_sec=timeout_sec,
            log_dir=self.log_dir,
        )

        try:
            result = await manager.run(
                command=args,
                cwd=self.work_dir,
                env=env,
                capture_output=True,
            )

            # If we rewrote to `python -m ...` and the module isn't installed, try to install it
            # into the Autopilot python environment and re-run (best-effort). This keeps Autopilot
            # "just working" without requiring the user to pre-install tools.
            install_attempts = 0
            while (
                not result["success"]
                and command_type in {"format", "lint", "tests", "uat"}
                and env is not None
                and self._looks_like_missing_python_module(result.get("output", "") or "")
                and install_attempts < 3
            ):
                module = self._missing_python_module_name(result.get("output", "") or "")
                if not module or not self._is_safe_module_name(module):
                    break
                if module in self._auto_installed:
                    break
                installed = await self._ensure_module_available(module=module)
                if not installed:
                    break
                self._auto_installed.add(module)
                install_attempts += 1
                result = await manager.run(
                    command=args,
                    cwd=self.work_dir,
                    env=env,
                    capture_output=True,
                )

            # Ruff is frequently used as a lint gate and Autopilot generates `tests/uat/` files.
            # If lint fails due to fixable issues in those generated files (common when older
            # Autopilot versions wrote unused imports), auto-fix them and retry once.
            if (
                not result["success"]
                and command_type == "lint"
                and env is not None
                and venv_python is not None
                and self._command_invokes_tool(command, tool="ruff")
            ):
                fixed = await self._auto_fix_ruff_uat_files(
                    python=venv_python,
                    env=env,
                    output=result.get("output", "") or "",
                )
                if fixed:
                    result = await manager.run(
                        command=args,
                        cwd=self.work_dir,
                        env=env,
                        capture_output=True,
                    )

            if (
                command_type in {"tests", "uat"}
                and self.allow_no_tests
                and result["exit_code"] == 5
            ):
                # Pytest returns exit code 5 when no tests are collected. With `-q` this can
                # produce no stdout, so key off the exit code (not output content).
                label = "UAT tests" if command_type == "uat" else "tests"
                logger.warning("No %s collected; treating as success", label)
                result["success"] = True
                result["exit_code"] = 0

            # Get output tail (last 20 lines)
            output_lines = result["output"].split("\n")
            output_tail = "\n".join(output_lines[-20:])

            return ValidationResult(
                command_type=command_type,
                success=result["success"],
                exit_code=result["exit_code"],
                output=output_tail,
                timed_out=result["timed_out"],
                stuck=result["stuck"],
            )

        except Exception as e:
            logger.error(f"Command execution failed: {e}")
            return ValidationResult(
                command_type=command_type,
                success=False,
                exit_code=None,
                output=f"Execution error: {e}",
            )

    async def _auto_fix_ruff_uat_files(
        self,
        python: Path,
        env: dict[str, str],
        output: str,
    ) -> bool:
        """Best-effort: run `ruff check --fix` + `ruff format` on UAT files referenced in output."""
        uat_paths = self._extract_uat_paths_from_ruff_output(output)
        if not uat_paths:
            return False

        existing = [p for p in uat_paths if (self.work_dir / p).exists()]
        if not existing:
            return False

        logger.warning("Auto-fixing ruff issues in generated UAT files: %s", ", ".join(existing))
        manager = SubprocessManager(timeout_sec=300, log_dir=self.log_dir)
        try:
            await manager.run(
                command=[str(python), "-m", "ruff", "check", "--fix", *existing],
                cwd=self.work_dir,
                env=env,
                capture_output=True,
            )
            await manager.run(
                command=[str(python), "-m", "ruff", "format", *existing],
                cwd=self.work_dir,
                env=env,
                capture_output=True,
            )
            return True
        except Exception as e:
            logger.warning("Failed to auto-fix UAT files with ruff: %s", e)
            return False

    @staticmethod
    def _extract_uat_paths_from_ruff_output(output: str) -> list[str]:
        # Ruff outputs file locations in a few formats, e.g.:
        #   --> tests/uat/test_task_5_uat.py:1:8
        #   tests/uat/test_task_5_uat.py:1:8: F401 ...
        # Also handle Windows-style paths in output.
        raw = set(
            m.replace("\\", "/")
            for m in re.findall(r"(?:tests/uat/|tests\\\\uat\\\\)[^\s:]+\.py", output)
        )
        return sorted(raw)

    @staticmethod
    def _rewrite_module_entrypoint(command: str, module: str, python_executable: str) -> str:
        """Rewrite leading `<module>` or `python -m <module>` to a specific python executable.

        Preserves patterns like `ENV=1 pytest -q` by only rewriting the first non-assignment token.
        """
        try:
            parts = shlex.split(command, posix=(os.name != "nt"))
        except Exception:
            return command

        if not parts:
            return command

        idx = 0
        for i, token in enumerate(parts):
            if token.startswith("-") or "=" not in token:
                idx = i
                break
            key = token.split("=", 1)[0]
            if not key or not key.replace("_", "").isalnum():
                idx = i
                break
        else:
            idx = len(parts)

        if idx >= len(parts):
            return command

        head = parts[idx]
        if head == module:
            parts[idx : idx + 1] = [python_executable, "-m", module]
        elif (
            head.startswith("python")
            and idx + 2 < len(parts)
            and parts[idx + 1] == "-m"
            and parts[idx + 2] == module
        ):
            parts[idx] = python_executable
        else:
            return command

        try:
            return shlex.join(parts)
        except Exception:
            return " ".join(parts)

    @staticmethod
    def _normalize_ruff_lint_command(command: str) -> str:
        """Normalize ruff lint commands to include an explicit subcommand.

        Newer ruff versions require `ruff check ...` for linting. Some configs still
        use `ruff <paths>` (or `python -m ruff <paths>`). When detected, rewrite to
        `ruff check ...` / `python -m ruff check ...`.
        """
        try:
            parts = shlex.split(command, posix=(os.name != "nt"))
        except Exception:
            return command

        if not parts:
            return command

        def is_global_help_or_version(token: str) -> bool:
            return token in {"-h", "--help", "-V", "--version"}

        known_subcommands = {"check", "format", "rule", "clean", "analyze", "lsp", "server"}

        changed = False
        i = 0
        while i < len(parts):
            if parts[i] == "ruff":
                ruff_idx = i
                next_idx = ruff_idx + 1
                if next_idx >= len(parts):
                    parts.extend(["check", "."])
                    changed = True
                    break
                nxt = parts[next_idx]
                if is_global_help_or_version(nxt) or nxt in known_subcommands:
                    i = next_idx + 1
                    continue
                parts.insert(next_idx, "check")
                changed = True
                i = next_idx + 2
                continue

            if (
                parts[i].startswith("python")
                and i + 2 < len(parts)
                and parts[i + 1] == "-m"
                and parts[i + 2] == "ruff"
            ):
                ruff_idx = i + 2
                next_idx = ruff_idx + 1
                if next_idx >= len(parts):
                    parts.extend(["check", "."])
                    changed = True
                    break
                nxt = parts[next_idx]
                if is_global_help_or_version(nxt) or nxt in known_subcommands:
                    i = next_idx + 1
                    continue
                parts.insert(next_idx, "check")
                changed = True
                i = next_idx + 2
                continue

            i += 1

        if not changed:
            return command

        try:
            return shlex.join(parts)
        except Exception:
            return " ".join(parts)

    @staticmethod
    def _looks_like_missing_python_module(output: str) -> bool:
        return "No module named " in output or "ModuleNotFoundError" in output

    @staticmethod
    def _missing_python_module_name(output: str) -> str | None:
        # Normalize common pytest tracebacks into a module name.
        # Example:
        #   ModuleNotFoundError: No module named 'fastapi'
        marker = "No module named "
        idx = output.find(marker)
        if idx == -1:
            return None
        rest = output[idx + len(marker) :].strip()
        if rest.startswith("'") and "'" in rest[1:]:
            return rest.split("'", 2)[1]
        if rest.startswith('"') and '"' in rest[1:]:
            return rest.split('"', 2)[1]
        # Fallback: take first token.
        return rest.split()[0] if rest else None

    @staticmethod
    def _is_safe_module_name(module: str) -> bool:
        # Best-effort guard against weird strings being treated as pip requirements.
        if not module or len(module) > 64:
            return False
        for ch in module:
            if not (ch.isalnum() or ch in {"_", "-", "."}):
                return False
        return True

    @staticmethod
    def _should_use_repo_venv(command_type: str, command: str) -> bool:
        """Return True if this validation command should run inside the repo venv.

        We only create/activate the repo venv when we detect Python tooling we manage
        (`ruff` / `pytest`). This avoids creating Python environments for non-Python repos
        (e.g., `npm test`).
        """
        if command_type in {"format", "lint"}:
            return ValidationRunner._command_invokes_tool(command, tool="ruff")
        if command_type in {"tests", "uat"}:
            return ValidationRunner._command_invokes_tool(command, tool="pytest")
        return False

    @staticmethod
    def _command_invokes_tool(command: str, tool: str) -> bool:
        """Detect if the command mentions `tool` or `python -m tool` anywhere.

        Commands are user-provided and can include shell chains like `cd backend && ruff check .`.
        """
        try:
            parts = shlex.split(command, posix=(os.name != "nt"))
        except Exception:
            return tool in command

        if not parts:
            return False

        for idx, token in enumerate(parts):
            if token == tool:
                return True
            if token.startswith("python") and idx + 2 < len(parts) and parts[idx + 1] == "-m":
                if parts[idx + 2] == tool:
                    return True
        return False

    async def _ensure_module_available(self, module: str) -> bool:
        """Ensure a module is importable by the repo venv (best-effort)."""
        python = await self._ensure_repo_venv()
        if python is None:
            return False

        logger.warning("Auto-installing missing module into repo venv: %s", module)
        manager = SubprocessManager(timeout_sec=300, log_dir=self.log_dir)
        try:
            result = await manager.run(
                command=[str(python), "-m", "pip", "install", "-q", module],
                cwd=self.work_dir,
                capture_output=True,
            )
            if not result.get("success"):
                logger.error("Failed to auto-install %s: %s", module, result.get("output", ""))
                return False
            return True
        except Exception as e:
            logger.error("Failed to auto-install %s: %s", module, e)
            return False

    async def _ensure_repo_venv(self) -> Path | None:
        """Create/boot a shared per-repo venv and return its python executable."""
        venv_python = self._venv_python_path()
        if self._venv_python and venv_python.exists():
            return self._venv_python

        lock_dir = self.venv_dir.parent / "venv.lock"
        start = time.time()
        while True:
            try:
                lock_dir.mkdir(parents=True, exist_ok=False)
                break
            except FileExistsError:
                if time.time() - start > 60:
                    break
                await self._sleep(0.2)

        try:
            venv_python = self._venv_python_path()
            if not venv_python.exists():
                self.venv_dir.parent.mkdir(parents=True, exist_ok=True)
                manager = SubprocessManager(timeout_sec=300, log_dir=self.log_dir)
                result = await manager.run(
                    command=[sys.executable, "-m", "venv", str(self.venv_dir)],
                    cwd=self.work_dir,
                    capture_output=True,
                )
                if not result.get("success"):
                    logger.error("Failed to create repo venv: %s", result.get("output", ""))
                    return None

            self._venv_python = self._venv_python_path()
            await self._bootstrap_venv_tools()
            await self._bootstrap_repo_python_projects()
            return self._venv_python
        finally:
            try:
                lock_dir.rmdir()
            except Exception:
                pass

    def _venv_python_path(self) -> Path:
        if os.name == "nt":
            return self.venv_dir / "Scripts" / "python.exe"
        return self.venv_dir / "bin" / "python"

    def _env_for_venv(self, venv_python: Path) -> dict[str, str]:
        env = os.environ.copy()
        bindir = str(venv_python.parent)
        env["VIRTUAL_ENV"] = str(self.venv_dir)
        env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
        env["PYTHONNOUSERSITE"] = "1"
        return env

    async def _bootstrap_venv_tools(self) -> None:
        python = self._venv_python_path()
        if not python.exists():
            return

        manager = SubprocessManager(timeout_sec=300, log_dir=self.log_dir)

        pip_ok = await manager.run(
            command=[str(python), "-m", "pip", "--version"],
            cwd=self.work_dir,
            capture_output=True,
        )
        if not pip_ok.get("success"):
            await manager.run(
                command=[str(python), "-m", "ensurepip", "--upgrade"],
                cwd=self.work_dir,
                capture_output=True,
            )

        await manager.run(
            command=[str(python), "-m", "pip", "install", "-q", "--upgrade", "pip"],
            cwd=self.work_dir,
            capture_output=True,
        )
        # Baseline tooling Autopilot runs across repos.
        await manager.run(
            command=[str(python), "-m", "pip", "install", "-q", "ruff", "pytest"],
            cwd=self.work_dir,
            capture_output=True,
        )

    async def _bootstrap_repo_python_projects(self) -> None:
        python = self._venv_python_path()
        if not python.exists():
            return

        marker = self.venv_dir / ".autopilot_projects_installed"
        if marker.exists():
            return

        candidates = [Path(".")]
        for sub in ("backend", "api", "server"):
            candidates.append(Path(sub))

        manager = SubprocessManager(timeout_sec=300, log_dir=self.log_dir)
        for rel in candidates:
            base = (self.work_dir / rel).resolve()
            if not base.exists() or not base.is_dir():
                continue

            pyproject = base / "pyproject.toml"
            requirements = base / "requirements.txt"

            if pyproject.exists():
                await manager.run(
                    command=[str(python), "-m", "pip", "install", "-q", "-e", str(base)],
                    cwd=self.work_dir,
                    capture_output=True,
                )
            elif requirements.exists():
                await manager.run(
                    command=[str(python), "-m", "pip", "install", "-q", "-r", str(requirements)],
                    cwd=self.work_dir,
                    capture_output=True,
                )

        try:
            marker.write_text("ok\n", encoding="utf-8")
        except Exception:
            pass

    @staticmethod
    async def _sleep(seconds: float) -> None:
        import asyncio

        await asyncio.sleep(seconds)

    def get_failure_summary(self, results: dict[str, ValidationResult]) -> str:
        """Generate summary of validation failures.

        Args:
            results: Dict of command_type -> ValidationResult

        Returns:
            Summary string
        """
        failures = []

        for cmd_type, result in results.items():
            if not result.success:
                failures.append(f"**{cmd_type.upper()} failed**:")
                failures.append(f"```\n{result.output}\n```")

        if not failures:
            return "All validations passed"

        return "\n\n".join(failures)
