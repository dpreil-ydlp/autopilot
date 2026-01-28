"""Validation runner for format, lint, tests, and UAT."""

import logging
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..utils.subprocess import SubprocessManager

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result of validation execution."""

    command_type: str  # format, lint, tests, uat
    success: bool
    exit_code: Optional[int]
    output: str
    timed_out: bool = False
    stuck: bool = False


@dataclass
class UATResult:
    """Result of UAT execution."""

    success: bool
    exit_code: Optional[int]
    output: str
    uat_file: Optional[Path]
    timed_out: bool = False
    stuck: bool = False


class ValidationRunner:
    """Run validation commands."""

    def __init__(
        self,
        work_dir: Path,
        timeout_sec: int = 120,
        log_dir: Optional[Path] = None,
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

    async def run_format(
        self,
        command: Optional[str],
        timeout_sec: Optional[int] = None,
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
        command: Optional[str],
        timeout_sec: Optional[int] = None,
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
        timeout_sec: Optional[int] = None,
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
        command: Optional[str],
        timeout_sec: Optional[int] = None,
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
        commands: dict[str, Optional[str]],
        timeout_sec: Optional[int] = None,
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
        # Parse command into arguments
        try:
            args = shlex.split(command)
        except ValueError as e:
            logger.error(f"Failed to parse command: {e}")
            return ValidationResult(
                command_type=command_type,
                success=False,
                exit_code=None,
                output=f"Command parse error: {e}",
            )

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
