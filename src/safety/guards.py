"""Safety guards and kill switches."""

import logging
from dataclasses import dataclass
from pathlib import Path

from ..utils.git import GitOps

logger = logging.getLogger(__name__)


class SafetyError(Exception):
    """Safety violation error."""

    pass


@dataclass
class ScopeCheckResult:
    """Result of scope validation."""

    valid: bool
    violations: list[str]
    diff_lines: int
    todo_count: int


class KillSwitch:
    """Kill switch detection and handling."""

    STOP_FILE = Path(".autopilot/AUTOPILOT_STOP")
    PAUSE_FILE = Path(".autopilot/AUTOPILOT_PAUSE")
    SKIP_REVIEW_FILE = Path(".autopilot/AUTOPILOT_SKIP_REVIEW")

    @classmethod
    def check_stop(cls) -> bool:
        """Check if stop is requested.

        Returns:
            True if AUTOPILOT_STOP file exists
        """
        exists = cls.STOP_FILE.exists()
        if exists:
            logger.warning("Kill switch detected: AUTOPILOT_STOP")
        return exists

    @classmethod
    def check_pause(cls) -> bool:
        """Check if pause is requested.

        Returns:
            True if AUTOPILOT_PAUSE file exists
        """
        exists = cls.PAUSE_FILE.exists()
        if exists:
            logger.info("Pause requested: AUTOPILOT_PAUSE")
        return exists

    @classmethod
    def check_skip_review(cls) -> bool:
        """Check if review should be skipped (emergency mode).

        Returns:
            True if AUTOPILOT_SKIP_REVIEW file exists
        """
        exists = cls.SKIP_REVIEW_FILE.exists()
        if exists:
            logger.warning("EMERGENCY MODE: Review skip requested - this will be logged")
        return exists

    @classmethod
    def clear_stop(cls) -> None:
        """Remove stop file."""
        if cls.STOP_FILE.exists():
            cls.STOP_FILE.unlink()
            logger.info("Cleared AUTOPILOT_STOP")

    @classmethod
    def clear_pause(cls) -> None:
        """Remove pause file."""
        if cls.PAUSE_FILE.exists():
            cls.PAUSE_FILE.unlink()
            logger.info("Cleared AUTOPILOT_PAUSE")

    @classmethod
    def clear_skip_review(cls) -> None:
        """Remove skip review file."""
        if cls.SKIP_REVIEW_FILE.exists():
            cls.SKIP_REVIEW_FILE.unlink()
            logger.info("Cleared AUTOPILOT_SKIP_REVIEW")


class ScopeGuard:
    """Validate code changes against safety constraints."""

    def __init__(
        self,
        git_ops: GitOps,
        allowed_paths: list[str] | None = None,
        denied_paths: list[str] | None = None,
        diff_lines_cap: int = 1000,
        max_todo_count: int = 0,
    ):
        """Initialize scope guard.

        Args:
            git_ops: Git operations wrapper
            allowed_paths: List of allowed path prefixes
            denied_paths: List of denied path prefixes
            diff_lines_cap: Maximum allowed diff lines
            max_todo_count: Maximum new TODOs allowed (0 = none)
        """
        self.git_ops = git_ops
        self.allowed_paths = allowed_paths or []
        self.denied_paths = denied_paths or []
        self.diff_lines_cap = diff_lines_cap
        self.max_todo_count = max_todo_count

    async def validate(self) -> ScopeCheckResult:
        """Validate current changes against safety constraints.

        Returns:
            ScopeCheckResult with validation details
        """
        violations = []

        # Check scope (allowed/denied paths)
        if self.allowed_paths or self.denied_paths:
            scope_result = await self.git_ops.validate_scope(
                allowed_paths=self.allowed_paths,
                denied_paths=self.denied_paths,
            )
            if not scope_result["valid"]:
                violations.extend(scope_result["violations"])

        # Check diff size
        diff_lines = await self.git_ops.count_lines_changed()
        if diff_lines > self.diff_lines_cap:
            violations.append(f"Diff too large: {diff_lines} lines (cap: {self.diff_lines_cap})")

        # Check TODOs
        if self.max_todo_count == 0:
            todo_count = await self.git_ops.check_todos()
            if todo_count > 0:
                violations.append(
                    f"New TODOs detected: {todo_count} (max allowed: {self.max_todo_count})"
                )
        else:
            todo_count = await self.git_ops.check_todos()
            if todo_count > self.max_todo_count:
                violations.append(
                    f"Too many new TODOs: {todo_count} (max allowed: {self.max_todo_count})"
                )

        valid = len(violations) == 0

        return ScopeCheckResult(
            valid=valid,
            violations=violations,
            diff_lines=diff_lines,
            todo_count=todo_count,
        )

    def raise_if_invalid(self, result: ScopeCheckResult) -> None:
        """Raise SafetyError if validation failed.

        Args:
            result: ScopeCheckResult

        Raises:
            SafetyError: If result is invalid
        """
        if not result.valid:
            error_msg = "Safety violations detected:\n" + "\n".join(
                f"  - {v}" for v in result.violations
            )
            raise SafetyError(error_msg)


class SafetyChecker:
    """Main safety checker combining all safety features."""

    def __init__(
        self,
        git_ops: GitOps,
        allowed_paths: list[str] | None = None,
        denied_paths: list[str] | None = None,
        diff_lines_cap: int = 1000,
        max_todo_count: int = 0,
    ):
        """Initialize safety checker.

        Args:
            git_ops: Git operations wrapper
            allowed_paths: List of allowed path prefixes
            denied_paths: List of denied path prefixes
            diff_lines_cap: Maximum allowed diff lines
            max_todo_count: Maximum new TODOs allowed
        """
        self.kill_switch = KillSwitch()
        self.scope_guard = ScopeGuard(
            git_ops=git_ops,
            allowed_paths=allowed_paths,
            denied_paths=denied_paths,
            diff_lines_cap=diff_lines_cap,
            max_todo_count=max_todo_count,
        )

    async def check_before_transition(self) -> None:
        """Check safety before state transition.

        Raises:
            SafetyError: If safety checks fail
        """
        # Check kill switches
        if self.kill_switch.check_stop():
            raise SafetyError("Stop requested via AUTOPILOT_STOP")

        if self.kill_switch.check_pause():
            raise SafetyError("Pause requested via AUTOPILOT_PAUSE")

    async def check_before_commit(self) -> ScopeCheckResult:
        """Check safety before committing changes.

        Returns:
            ScopeCheckResult with validation details

        Raises:
            SafetyError: If scope validation fails
        """
        result = await self.scope_guard.validate()
        self.scope_guard.raise_if_invalid(result)
        return result

    def should_skip_review(self) -> bool:
        """Check if review should be skipped (emergency mode).

        Returns:
            True if AUTOPILOT_SKIP_REVIEW exists
        """
        return self.kill_switch.check_skip_review()
