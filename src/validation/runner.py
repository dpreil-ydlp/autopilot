"""Validation runner for format, lint, tests, and UAT."""

import logging
import os
import shlex
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
        # Some task plans emit `pytest ...` commands, but the `pytest` entrypoint may not be on
        # PATH in non-interactive shells. Prefer `python3 -m pytest` when we can detect it.
        if command_type in {"tests", "uat"}:
            command = self._rewrite_pytest_entrypoint(command)

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
                capture_output=True,
            )

            if (
                command_type == "tests"
                and self.allow_no_tests
                and result["exit_code"] == 5
            ):
                # Pytest returns exit code 5 when no tests are collected. With `-q` this can
                # produce no stdout, so key off the exit code (not output content).
                logger.warning("No tests collected; treating as success")
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

    @staticmethod
    def _rewrite_pytest_entrypoint(command: str) -> str:
        """Rewrite leading `pytest` invocation to `python3 -m pytest` when possible.

        This preserves common patterns like `ENV=1 pytest -q` by only rewriting the first
        non-assignment token.
        """
        try:
            parts = shlex.split(command, posix=(os.name != "nt"))
        except Exception:
            return command

        if not parts:
            return command

        idx = 0
        for i, token in enumerate(parts):
            # Stop at first non KEY=VALUE assignment.
            if token.startswith("-") or "=" not in token:
                idx = i
                break
            key = token.split("=", 1)[0]
            if not key or not key.replace("_", "").isalnum():
                idx = i
                break
        else:
            idx = len(parts)

        if idx < len(parts) and parts[idx] == "pytest":
            parts[idx : idx + 1] = ["python3", "-m", "pytest"]
            try:
                return shlex.join(parts)
            except Exception:
                return " ".join(parts)

        return command

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
