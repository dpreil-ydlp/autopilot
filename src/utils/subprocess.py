"""Subprocess management with timeouts and stuck detection."""

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class SubprocessError(Exception):
    """Subprocess execution error."""

    def __init__(
        self,
        message: str,
        exit_code: Optional[int] = None,
        timed_out: bool = False,
        stuck: bool = False,
    ):
        super().__init__(message)
        self.exit_code = exit_code
        self.timed_out = timed_out
        self.stuck = stuck


class SubprocessManager:
    """Managed subprocess execution with timeouts and stuck detection."""

    def __init__(
        self,
        timeout_sec: int,
        stuck_no_output_sec: Optional[int] = None,
        log_dir: Optional[Path] = None,
    ):
        """Initialize subprocess manager.

        Args:
            timeout_sec: Hard timeout for process
            stuck_no_output_sec: Stuck detection threshold (None to disable)
            log_dir: Directory for process logs
        """
        self.timeout_sec = timeout_sec
        self.stuck_no_output_sec = stuck_no_output_sec
        self.log_dir = log_dir

    async def run(
        self,
        command: list[str],
        cwd: Optional[Path] = None,
        env: Optional[dict[str, str]] = None,
        capture_output: bool = True,
    ) -> dict:
        """Run command with timeout and stuck detection.

        Args:
            command: Command and arguments
            cwd: Working directory
            env: Environment variables
            capture_output: Whether to capture stdout/stderr

        Returns:
            Result dict with keys:
                - success: bool
                - output: str
                - exit_code: int
                - timed_out: bool
                - stuck: bool

        Raises:
            SubprocessError: On execution failure
        """
        logger.info(f"Running command: {' '.join(command)}")

        # Prepare log file if configured
        log_path = None
        if self.log_dir:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_path = self.log_dir / f"cmd_{timestamp}.log"

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=cwd,
                env=env,
                stdout=asyncio.subprocess.PIPE if capture_output else None,
                stderr=asyncio.subprocess.PIPE if capture_output else None,
            )

            # Track output for stuck detection
            last_output_time = {"value": datetime.now()}
            output_lines = []

            if capture_output:
                # Read output with timeout and stuck detection
                timeout_task = asyncio.create_task(
                    self._read_with_timeout(
                        process,
                        last_output_time,
                        output_lines,
                        log_path,
                    )
                )
                wait_task = asyncio.create_task(process.wait())

                # Wait for either completion or timeout
                done, pending = await asyncio.wait(
                    [timeout_task, wait_task],
                    timeout=self.timeout_sec,
                    return_when=asyncio.FIRST_COMPLETED,
                )

                # Cancel pending tasks
                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

                # Check results
                if wait_task in done:
                    # Process completed
                    exit_code = process.returncode
                    output = "".join(output_lines)
                    timed_out = False
                    stuck = False
                else:
                    # Timeout occurred
                    if self.stuck_no_output_sec:
                        time_since_output = (
                            datetime.now() - last_output_time["value"]
                        ).total_seconds()
                        stuck = time_since_output > self.stuck_no_output_sec
                    else:
                        stuck = False

                    # Kill process
                    try:
                        process.kill()
                        await process.wait()
                    except Exception:
                        pass

                    timed_out = True
                    output = "".join(output_lines)
                    exit_code = None

                logger.info(f"Command completed: exit_code={exit_code}, timed_out={timed_out}, stuck={stuck}")

            else:
                # No output capture, just wait with timeout
                try:
                    exit_code = await asyncio.wait_for(process.wait(), timeout=self.timeout_sec)
                    output = ""
                    timed_out = False
                    stuck = False
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
                    timed_out = True
                    output = ""
                    exit_code = None
                    stuck = False

            return {
                "success": exit_code == 0,
                "output": output,
                "exit_code": exit_code,
                "timed_out": timed_out,
                "stuck": stuck,
            }

        except FileNotFoundError as e:
            raise SubprocessError(f"Command not found: {command[0]}")
        except Exception as e:
            raise SubprocessError(f"Subprocess error: {e}")

    async def _read_with_timeout(
        self,
        process: asyncio.subprocess.Process,
        last_output_time: dict[str, datetime],
        output_lines: list[str],
        log_path: Optional[Path] = None,
    ) -> None:
        """Read process output with stuck detection.

        Args:
            process: Process to read from
            last_output_time: Updated when output received
            output_lines: Accumulates output lines
            log_path: Optional log file path
        """
        log_file = None
        if log_path:
            log_file = open(log_path, "w")

        try:
            # Read stdout and stderr concurrently
            readers = []
            if process.stdout:
                readers.append(self._read_stream(process.stdout, last_output_time, output_lines, log_file))
            if process.stderr:
                readers.append(self._read_stream(process.stderr, last_output_time, output_lines, log_file))

            # Wait for all streams to close
            await asyncio.gather(*readers)

        finally:
            if log_file:
                log_file.close()

    async def _read_stream(
        self,
        stream: asyncio.StreamReader,
        last_output_time: dict[str, datetime],
        output_lines: list[str],
        log_file: Optional[object] = None,
    ) -> None:
        """Read from a single stream.

        Args:
            stream: Stream to read
            last_output_time: Updated when output received
            output_lines: Accumulates output lines
            log_file: Optional log file
        """
        while True:
            line = await stream.readline()
            if not line:
                break

            line_str = line.decode("utf-8", errors="replace")
            output_lines.append(line_str)
            last_output_time["value"] = datetime.now()

            if log_file:
                log_file.write(line_str)
                log_file.flush()
