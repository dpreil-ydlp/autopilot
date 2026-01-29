"""Claude Code CLI agent wrapper."""

import json
import logging
import time
from pathlib import Path

from ..utils.subprocess import SubprocessError, SubprocessManager
from .base import AgentError, BaseAgent

logger = logging.getLogger(__name__)


class ClaudeAgent(BaseAgent):
    """Claude Code CLI agent implementation."""

    def __init__(self, config: dict):
        """Initialize Claude agent.

        Args:
            config: Builder config with cli_path, max_retries, allowed_skills
        """
        super().__init__(config)
        self.cli_path = config.get("cli_path", "claude")
        self.max_retries = config.get("max_retries", 1)
        self.allowed_skills = config.get("allowed_skills", [])
        self.permission_mode = config.get("permission_mode", "dontAsk")
        self.stream_output = config.get("stream_output", True)
        self.stream_log_interval_sec = config.get("stream_log_interval_sec", 1.5)
        self.system_prompt = config.get("system_prompt")

    async def execute(
        self,
        prompt: str,
        timeout_sec: int,
        work_dir: Path | None = None,
    ) -> dict:
        """Execute build with Claude Code CLI.

        Args:
            prompt: Build prompt/task
            timeout_sec: Execution timeout
            work_dir: Working directory

        Returns:
            Result dict
        """
        # Build command using print mode with non-interactive permissions
        wrapped_prompt = self._wrap_prompt(prompt)
        if self.stream_output:
            command = [
                self.cli_path,
                "--permission-mode",
                self.permission_mode,
                "--print",
                "--verbose",
                "--output-format",
                "stream-json",
                "--include-partial-messages",
                wrapped_prompt,
            ]
        else:
            command = [
                self.cli_path,
                "--permission-mode",
                self.permission_mode,
                "--print",
                wrapped_prompt,
            ]
        if self.system_prompt:
            command.insert(1, "--system-prompt")
            command.insert(2, self.system_prompt)

        retries = 0
        last_error = None

        while retries <= self.max_retries:
            try:
                manager = SubprocessManager(timeout_sec=timeout_sec)
                stream_state = {
                    "result": None,
                    "text_parts": [],
                    "buffer": "",
                    "last_flush": time.time(),
                }

                def handle_line(line: str) -> None:
                    if not self.stream_output:
                        return
                    try:
                        payload = json.loads(line)
                    except Exception:
                        return

                    payload_type = payload.get("type")
                    if payload_type == "stream_event":
                        event = payload.get("event", {})
                        if event.get("type") == "content_block_delta":
                            delta = event.get("delta", {})
                            if delta.get("type") == "text_delta":
                                text = delta.get("text", "")
                                if text:
                                    stream_state["text_parts"].append(text)
                                    stream_state["buffer"] += text
                    elif payload_type == "result":
                        stream_state["result"] = payload.get("result")
                    elif payload_type == "assistant":
                        message = payload.get("message", {})
                        contents = message.get("content", [])
                        if isinstance(contents, list):
                            for block in contents:
                                if block.get("type") == "text" and block.get("text"):
                                    stream_state["text_parts"].append(block["text"])

                    now = time.time()
                    buffer_text = stream_state["buffer"]
                    if buffer_text and (
                        "\n" in buffer_text
                        or len(buffer_text) >= 200
                        or now - stream_state["last_flush"] >= self.stream_log_interval_sec
                    ):
                        logger.info("claude> %s", buffer_text.rstrip())
                        stream_state["buffer"] = ""
                        stream_state["last_flush"] = now

                result = await manager.run(
                    command,
                    cwd=work_dir,
                    on_output_line=handle_line if self.stream_output else None,
                )

                if self.stream_output and stream_state["buffer"]:
                    logger.info("claude> %s", stream_state["buffer"].rstrip())
                    stream_state["buffer"] = ""

                output_text = result["output"]
                if self.stream_output:
                    output_text = (
                        self._resolve_streamed_text(output_text, stream_state) or output_text
                    )
                    result["output"] = output_text

                if result["success"] or output_text.strip():
                    diff = self._extract_diff(output_text)
                    if diff:
                        await self._apply_diff(diff, work_dir)
                    elif "NO_CHANGES" not in output_text:
                        # If Claude edited files directly, accept working tree changes
                        status = await manager.run(
                            ["git", "status", "--porcelain"],
                            cwd=work_dir,
                        )
                        if status["output"].strip():
                            logger.warning(
                                "Claude output missing diff but working tree has changes; continuing"
                            )
                        else:
                            raise AgentError(
                                "Build output did not include a unified diff or NO_CHANGES marker."
                            )

                    # Try to extract JSON summary
                    summary = self._extract_summary(output_text)
                    return {
                        **result,
                        "success": True,
                        "summary": summary,
                    }

                # Build failed with no usable output
                raise AgentError(f"Build failed: {result['output'][-200:]}")

            except SubprocessError as e:
                last_error = e
                if e.timed_out or e.stuck:
                    logger.warning(f"Build timed out or stuck (retry {retries}/{self.max_retries})")
                    retries += 1
                else:
                    # Non-timeout error, don't retry
                    raise AgentError(f"Build subprocess error: {e}")

        # All retries exhausted
        raise AgentError(f"Build failed after {retries} retries: {last_error}")

    async def review(
        self,
        diff: str,
        validation_output: str,
        timeout_sec: int,
        work_dir: Path | None = None,
        context: str | None = None,
    ) -> dict:
        """Claude doesn't support review mode - use Codex agent."""
        raise NotImplementedError("Use Codex agent for review")

    async def plan(
        self,
        plan_content: str,
        timeout_sec: int,
        work_dir: Path | None = None,
        context: str | None = None,
    ) -> dict:
        """Claude doesn't support planning mode - use Codex agent."""
        raise NotImplementedError("Use Codex agent for planning")

    async def generate_uat(
        self,
        task_content: str,
        diff: str,
        timeout_sec: int,
        work_dir: Path | None = None,
        context: str | None = None,
    ) -> str:
        """Claude doesn't support UAT generation - use Codex agent."""
        raise NotImplementedError("Use Codex agent for UAT generation")

    def _extract_summary(self, output: str) -> dict | None:
        """Extract JSON summary from Claude output.

        Args:
            output: Claude CLI output

        Returns:
            Parsed summary dict or None
        """
        # Look for JSON in output
        # Claude Code CLI may output structured summary
        try:
            # Try to find JSON blocks
            lines = output.split("\n")
            json_start = -1
            for i, line in enumerate(lines):
                if line.strip().startswith("{") or line.strip().startswith("["):
                    json_start = i
                    break

            if json_start >= 0:
                # Try to parse JSON
                json_text = "\n".join(lines[json_start:])
                return json.loads(json_text)

        except (json.JSONDecodeError, Exception) as e:
            logger.debug(f"Failed to extract JSON summary: {e}")

        return None

    def _wrap_prompt(self, prompt: str) -> str:
        """Wrap prompt with diff-only instruction."""
        return (
            "You are running in non-interactive mode. "
            "Output ONLY a unified git diff that can be applied with `git apply`. "
            "Do NOT include commentary, explanations, or code fences. "
            "Your output must start with 'diff --git' or '--- a/'. "
            "If no changes are needed, output exactly: NO_CHANGES.\n\n"
            f"{prompt}"
        )

    def _extract_diff(self, output: str) -> str:
        """Extract a unified diff from Claude output."""

        def _looks_like_diff(text: str) -> bool:
            return "diff --git" in text or ("\n--- " in f"\n{text}" and "\n+++ " in f"\n{text}")

        # Prefer fenced diff blocks if present
        if "```" in output:
            sections = output.split("```")
            diff_blocks = []
            for i in range(1, len(sections), 2):
                block = sections[i].strip()
                if _looks_like_diff(block):
                    diff_blocks.append(block)
            if diff_blocks:
                return diff_blocks[-1]

        # Fallback: find first diff-like line in raw output
        lines = output.splitlines()
        last_idx = None
        for idx, line in enumerate(lines):
            if line.startswith("diff --git"):
                last_idx = idx
            if line.startswith("--- "):
                # ensure next line is +++
                if idx + 1 < len(lines) and lines[idx + 1].startswith("+++ "):
                    last_idx = idx

        if last_idx is not None:
            return "\n".join(lines[last_idx:]).strip()

        return ""

    def _resolve_streamed_text(self, output: str, stream_state: dict) -> str:
        """Resolve streamed JSON output into plain text."""
        if stream_state.get("result"):
            return stream_state["result"] or ""
        text = "".join(stream_state.get("text_parts", [])).strip()
        if text:
            return text

        # Fallback: parse from output lines if state is empty
        parts: list[str] = []
        result_text = ""
        for line in output.splitlines():
            try:
                payload = json.loads(line)
            except Exception:
                continue
            if payload.get("type") == "result":
                result_text = payload.get("result", "") or result_text
            elif payload.get("type") == "assistant":
                message = payload.get("message", {})
                contents = message.get("content", [])
                if isinstance(contents, list):
                    for block in contents:
                        if block.get("type") == "text" and block.get("text"):
                            parts.append(block["text"])
            elif payload.get("type") == "stream_event":
                event = payload.get("event", {})
                if event.get("type") == "content_block_delta":
                    delta = event.get("delta", {})
                    if delta.get("type") == "text_delta":
                        parts.append(delta.get("text", ""))
        if result_text:
            return result_text
        return "".join(parts).strip()

    async def _apply_diff(self, diff: str, work_dir: Path | None) -> None:
        """Apply diff with git apply in working directory."""
        target_dir = work_dir or Path.cwd()
        patch_dir = target_dir / ".autopilot" / "patches"
        patch_dir.mkdir(parents=True, exist_ok=True)
        patch_path = patch_dir / f"claude_{int(time.time())}.diff"
        patch_text = self._sanitize_diff(diff)
        if not patch_text.endswith("\n"):
            patch_text = f"{patch_text}\n"
        patch_path.write_text(patch_text, encoding="utf-8")

        manager = SubprocessManager(timeout_sec=60)
        result = await manager.run(
            ["git", "apply", "--whitespace=nowarn", str(patch_path)],
            cwd=target_dir,
        )
        if result["success"]:
            return

        # Fallback to 3-way apply for patch context mismatches
        result = await manager.run(
            ["git", "apply", "--3way", "--whitespace=nowarn", str(patch_path)],
            cwd=target_dir,
        )
        if not result["success"]:
            # If Claude applied edits directly, accept working tree changes
            status = await manager.run(
                ["git", "status", "--porcelain"],
                cwd=target_dir,
            )
            if status["output"].strip():
                logger.warning(
                    "Claude diff failed to apply but working tree has changes; continuing"
                )
                return
            raise AgentError(f"Failed to apply Claude diff: {result['output']}")

    def _sanitize_diff(self, diff: str) -> str:
        """Normalize diff lines to reduce corruption from missing prefixes."""
        lines = diff.splitlines()
        sanitized: list[str] = []
        in_hunk = False

        for line in lines:
            if line.startswith("diff --git"):
                in_hunk = False
                sanitized.append(line)
                continue
            if line.startswith(("index ", "deleted file mode", "new file mode")):
                sanitized.append(line)
                continue
            if line.startswith(("--- ", "+++ ")):
                sanitized.append(line)
                continue
            if line.startswith("@@"):
                in_hunk = True
                sanitized.append(line)
                continue
            if line.startswith("\\ No newline"):
                sanitized.append(line)
                continue

            if in_hunk:
                if line.startswith(("+", "-", " ")):
                    sanitized.append(line)
                else:
                    sanitized.append(f"+{line}")
            else:
                sanitized.append(line)

        return "\n".join(sanitized)
