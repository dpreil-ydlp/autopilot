"""Subprocess management with timeouts and stuck detection."""

import asyncio
import logging
import os
import shlex
import shutil
import signal
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class SubprocessError(Exception):
    """Subprocess execution error."""

    def __init__(
        self,
        message: str,
        exit_code: int | None = None,
        timed_out: bool = False,
        stuck: bool = False,
    ):
        super().__init__(message)
        self.exit_code = exit_code
        self.timed_out = timed_out
        self.stuck = stuck


class SubprocessManager:
    """Managed subprocess execution with timeouts and stuck detection."""

    @staticmethod
    async def _terminate_process(
        process: asyncio.subprocess.Process,
        timeout_sec: float = 2.0,
    ) -> None:
        """Terminate a subprocess and its children (best-effort).

        Many commands (e.g. `npm run dev`) spawn child processes. If we only kill the parent,
        orphaned children can keep consuming RAM/disk. On POSIX we start a new session and kill
        the whole process group.
        """
        if process.returncode is not None:
            return

        # Try graceful termination first.
        try:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
        except ProcessLookupError:
            return
        except Exception:
            try:
                process.terminate()
            except Exception:
                pass

        try:
            await asyncio.wait_for(process.wait(), timeout=timeout_sec)
            return
        except Exception:
            pass

        # Escalate.
        try:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            return
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

        try:
            await asyncio.wait_for(process.wait(), timeout=timeout_sec)
        except Exception:
            pass

    def __init__(
        self,
        timeout_sec: int,
        stuck_no_output_sec: int | None = None,
        log_dir: Path | None = None,
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
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        capture_output: bool = True,
        on_output_line: Callable[[str], None] | None = None,
        stdin: str | None = None,
    ) -> dict:
        """Run command with timeout and stuck detection.

        Args:
            command: Command and arguments
            cwd: Working directory
            env: Environment variables
            capture_output: Whether to capture stdout/stderr
            stdin: Optional string to write to stdin

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
        logger.info("Running command: %s", self._format_command_for_log(command))

        # Prepare log file if configured
        log_path = None
        if self.log_dir:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_path = self.log_dir / f"cmd_{timestamp}.log"

        try:
            process: asyncio.subprocess.Process | None = None
            read_task: asyncio.Task[None] | None = None
            try:
                logger.info(f"Creating subprocess with cwd={cwd}, capture_output={capture_output}, stdin={'yes' if stdin else 'no'}")
                process = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=cwd,
                    env=env,
                    start_new_session=(os.name != "nt"),
                    stdin=asyncio.subprocess.PIPE if stdin else None,
                    stdout=asyncio.subprocess.PIPE if capture_output else None,
                    stderr=asyncio.subprocess.PIPE if capture_output else None,
                )
                logger.info(f"Subprocess created with PID={process.pid}")

                # Write to stdin if provided
                if stdin and process.stdin:
                    process.stdin.write(stdin.encode('utf-8'))
                    await process.stdin.drain()
                    process.stdin.close()
                    logger.info(f"Wrote {len(stdin)} bytes to stdin")
            except FileNotFoundError:
                # If the caller provided a stripped env (common when overriding HOME),
                # ensure PATH is present so executables like `git` can be resolved.
                retry_env = None
                if env is not None and "PATH" not in env:
                    inherited_path = os.environ.get("PATH")
                    if inherited_path:
                        retry_env = dict(env)
                        retry_env["PATH"] = inherited_path

                resolved = None
                if not os.path.isabs(command[0]):
                    resolved = shutil.which(
                        command[0], path=(retry_env or env or os.environ).get("PATH")
                    )
                if resolved:
                    command = [resolved, *command[1:]]

                process = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=cwd,
                    env=retry_env or env,
                    start_new_session=(os.name != "nt"),
                    stdout=asyncio.subprocess.PIPE if capture_output else None,
                    stderr=asyncio.subprocess.PIPE if capture_output else None,
                )

            # Track output for stuck detection
            last_output_time = {"value": datetime.now()}
            output_lines = []

            if capture_output:
                # Read output concurrently while waiting on process completion. Do NOT treat the
                # output reader completing first as a timeout (process.wait() can lag slightly).
                read_task = asyncio.create_task(
                    self._read_with_timeout(
                        process,
                        last_output_time,
                        output_lines,
                        log_path,
                        on_output_line,
                    )
                )

                try:
                    logger.info(f"Waiting for process {process.pid} to complete (timeout={self.timeout_sec}s)...")
                    await asyncio.wait_for(process.wait(), timeout=self.timeout_sec)
                    timed_out = False
                    stuck = False
                    exit_code = process.returncode
                    logger.info(f"Process {process.pid} completed with exit_code={exit_code}, output_lines={len(output_lines)}")
                except TimeoutError:
                    if self.stuck_no_output_sec:
                        time_since_output = (
                            datetime.now() - last_output_time["value"]
                        ).total_seconds()
                        stuck = time_since_output > self.stuck_no_output_sec
                    else:
                        stuck = False

                    await self._terminate_process(process)
                    timed_out = True
                    exit_code = None

                # Give the reader a moment to drain any remaining buffered output.
                try:
                    await asyncio.wait_for(read_task, timeout=2.0)
                except Exception:
                    read_task.cancel()
                    try:
                        await read_task
                    except Exception:
                        pass

                output = "".join(output_lines)

                logger.info(
                    f"Command completed: exit_code={exit_code}, timed_out={timed_out}, stuck={stuck}"
                )

            else:
                # No output capture, just wait with timeout
                try:
                    exit_code = await asyncio.wait_for(process.wait(), timeout=self.timeout_sec)
                    output = ""
                    timed_out = False
                    stuck = False
                except TimeoutError:
                    await self._terminate_process(process)
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

        except FileNotFoundError:
            # FileNotFoundError can mean either the executable is missing from PATH or the cwd
            # does not exist. Distinguish the two so the caller can recover correctly.
            if cwd is not None and not Path(cwd).exists():
                raise SubprocessError(
                    f"Working directory not found: {cwd} (while running: {command[0]})"
                )
            raise SubprocessError(f"Command not found: {command[0]}")
        except asyncio.CancelledError:
            # Ensure we do not leak subprocess transports, which can surface as
            # "Exception ignored ... Event loop is closed" at shutdown.
            try:
                if read_task is not None and not read_task.done():
                    read_task.cancel()
                    try:
                        await read_task
                    except Exception:
                        pass
                if process is not None:
                    await self._terminate_process(process)
            finally:
                raise
        except KeyboardInterrupt:
            # Mirror the cancellation cleanup for Ctrl+C so we don't leak transports on abort.
            try:
                if read_task is not None and not read_task.done():
                    read_task.cancel()
                    try:
                        await read_task
                    except Exception:
                        pass
                if process is not None:
                    await self._terminate_process(process)
            finally:
                raise
        except Exception as e:
            raise SubprocessError(f"Subprocess error: {e}")

    @staticmethod
    def _format_command_for_log(command: list[str]) -> str:
        """Format a command for logs without dumping huge prompts."""
        if not command:
            return ""

        redacted = list(command)
        if redacted[0] in {"codex", "claude"} and len(redacted) >= 2:
            # The last argument is typically a large prompt; redact it if big.
            last_idx = len(redacted) - 1
            if len(redacted[last_idx]) > 200:
                redacted[last_idx] = f"<prompt {len(command[last_idx])} chars>"

        parts: list[str] = []
        max_args = 12
        max_arg_len = 200
        for i, arg in enumerate(redacted):
            if i >= max_args:
                parts.append("...")
                break
            if len(arg) > max_arg_len:
                arg = arg[:max_arg_len] + "..."
            parts.append(shlex.quote(arg))
        return " ".join(parts)

    async def _read_with_timeout(
        self,
        process: asyncio.subprocess.Process,
        last_output_time: dict[str, datetime],
        output_lines: list[str],
        log_path: Path | None = None,
        on_output_line: Callable[[str], None] | None = None,
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
                logger.info(f"Starting stdout reader for PID={process.pid}")
                readers.append(
                    self._read_stream(
                        process.stdout,
                        last_output_time,
                        output_lines,
                        log_file,
                        on_output_line,
                        stream_name="stdout",
                    )
                )
            if process.stderr:
                logger.info(f"Starting stderr reader for PID={process.pid}")
                readers.append(
                    self._read_stream(
                        process.stderr,
                        last_output_time,
                        output_lines,
                        log_file,
                        on_output_line,
                        stream_name="stderr",
                    )
                )

            # Wait for all streams to close
            logger.info(f"Waiting for {len(readers)} stream(s) to close for PID={process.pid}")
            await asyncio.gather(*readers)
            logger.info(f"All streams closed for PID={process.pid}")

        finally:
            if log_file:
                log_file.close()

    async def _read_stream(
        self,
        stream: asyncio.StreamReader,
        last_output_time: dict[str, datetime],
        output_lines: list[str],
        log_file: object | None = None,
        on_output_line: Callable[[str], None] | None = None,
        stream_name: str = "unknown",
    ) -> None:
        """Read from a single stream.

        Args:
            stream: Stream to read
            last_output_time: Updated when output received
            output_lines: Accumulates output lines
            log_file: Optional log file
            stream_name: Name of stream (stdout/stderr) for logging
        """
        line_count = 0
        logger.info(f"Starting to read from {stream_name} stream")
        while True:
            line = await stream.readline()
            if not line:
                logger.info(f"{stream_name} stream closed after {line_count} lines")
                break

            line_str = line.decode("utf-8", errors="replace")
            output_lines.append(line_str)
            last_output_time["value"] = datetime.now()
            if on_output_line:
                on_output_line(line_str)

            if log_file:
                log_file.write(line_str)
                log_file.flush()

            line_count += 1
            # Log first line and every 100 lines to track progress
            if line_count == 1 or line_count % 100 == 0:
                logger.info(f"Read {line_count} lines from {stream_name} (last line: {line_str[:100]!r}...)")
