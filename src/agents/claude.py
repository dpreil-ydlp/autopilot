"""Claude Code CLI agent wrapper."""

import json
import logging
import tempfile
import time
from pathlib import Path

from ..utils.subprocess import SubprocessError, SubprocessManager
from .base import AgentError, BaseAgent

logger = logging.getLogger(__name__)


class ClaudeAgent(BaseAgent):
    """Claude Code CLI agent implementation."""

    @staticmethod
    def _status_has_non_artifact_changes(porcelain: str) -> bool:
        """Return True when `git status --porcelain` includes non-artifact paths."""

        def extract_path(line: str) -> str:
            payload = line[3:].strip() if len(line) >= 4 else line.strip()
            if " -> " in payload:
                payload = payload.split(" -> ", 1)[1].strip()
            return payload

        for raw in porcelain.splitlines():
            line = raw.rstrip()
            if not line:
                continue
            path = extract_path(line)
            if not path:
                continue

            # Ignore Autopilot artifacts (patches, logs, shared venv, etc.).
            if path == ".autopilot" or path.startswith(".autopilot/"):
                continue
            if path == "logs" or path.startswith("logs/"):
                continue
            if path == ".pytest_cache" or path.startswith(".pytest_cache/"):
                continue
            if "/__pycache__/" in f"/{path}/" or path.endswith((".pyc", ".pyo")):
                continue

            return True

        return False

    @classmethod
    def _porcelain_non_artifact_entries(cls, porcelain: str) -> dict[str, str]:
        """Return {path: status_code} filtered to non-artifact paths."""

        def extract_path(line: str) -> str:
            payload = line[3:].strip() if len(line) >= 4 else line.strip()
            if " -> " in payload:
                payload = payload.split(" -> ", 1)[1].strip()
            return payload

        entries: dict[str, str] = {}
        for raw in porcelain.splitlines():
            line = raw.rstrip()
            if not line:
                continue
            code = line[:2]
            path = extract_path(line)
            if not path:
                continue

            if path == ".autopilot" or path.startswith(".autopilot/"):
                continue
            if path == "logs" or path.startswith("logs/"):
                continue
            if path == ".pytest_cache" or path.startswith(".pytest_cache/"):
                continue
            if "/__pycache__/" in f"/{path}/" or path.endswith((".pyc", ".pyo")):
                continue

            entries[path] = code

        return entries

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
        retries = 0
        last_error = None

        while retries <= self.max_retries:
            try:
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

                manager = SubprocessManager(timeout_sec=timeout_sec)
                pre_status_entries: dict[str, str] = {}
                if work_dir is not None:
                    try:
                        status_mgr = SubprocessManager(timeout_sec=30)
                        pre = await status_mgr.run(
                            ["git", "status", "--porcelain"],
                            cwd=work_dir,
                        )
                        pre_status_entries = self._porcelain_non_artifact_entries(
                            pre.get("output", "") or ""
                        )
                    except Exception:
                        pre_status_entries = {}

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
                        try:
                            await self._apply_diff(diff, work_dir)
                        except AgentError as e:
                            last_error = e
                            if retries < self.max_retries:
                                retries += 1
                                prompt = "\n\n".join(
                                    [
                                        prompt,
                                        "## Patch Apply Failure",
                                        "The previous diff failed to apply. Please regenerate a unified diff",
                                        "against the CURRENT repository state. Ensure hunks match existing files.",
                                        f"Error:\n{e}",
                                    ]
                                )
                                continue
                            raise
                    elif "NO_CHANGES" not in output_text:
                        # If Claude edited files directly, accept only if the non-artifact working
                        # tree changed relative to the pre-run snapshot. This avoids false-positive
                        # successes when only `.autopilot/patches` was written.
                        status = await manager.run(
                            ["git", "status", "--porcelain"],
                            cwd=work_dir,
                        )
                        post_status_entries = self._porcelain_non_artifact_entries(
                            status.get("output", "") or ""
                        )
                        if post_status_entries and post_status_entries != pre_status_entries:
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
            except AgentError as e:
                last_error = e
                if retries < self.max_retries:
                    retries += 1
                    continue
                raise

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
        """Review code changes using Claude Code CLI.

        Args:
            diff: Git diff of changes
            validation_output: Validation output
            timeout_sec: Review timeout
            work_dir: Working directory

        Returns:
            Review dict with verdict, feedback, issues
        """
        context_block = f"\n## Task Context\n{context}\n" if context else ""
        prompt = f"""Review the following code changes and validation output.{context_block}

Important: Do NOT scan the repo or look for AGENTS.md. Use only the inputs below.

## Git Diff
{diff}

## Validation Output
{validation_output}

Provide your review as JSON with this structure:
{{
  "verdict": "approve" | "request_changes",
  "feedback": "Detailed feedback explaining your verdict",
  "issues": ["list of specific issues found"]
}}

Consider:
- Code quality and style
- Test coverage
- Edge cases
- Security concerns
- Performance implications
- Alignment with requirements

Output ONLY the JSON, no other text."""

        manager = SubprocessManager(timeout_sec=timeout_sec)
        result = await manager.run(
            command=[
                self.cli_path,
                "--permission-mode", self.permission_mode,
                "--print",
                "--verbose",
                "--output-format", "stream-json",
                "--include-partial-messages",
                prompt,
            ],
            cwd=work_dir or Path.cwd(),
        )

        if not result["success"]:
            raise AgentError(f"Claude review failed: {result['output']}")

        # Extract JSON from output
        output = result["output"]
        review_data = self._extract_summary(output)

        if review_data is None:
            # Fallback: approve if no issues found
            return {
                "verdict": "approve",
                "feedback": "No obvious issues found in the changes.",
                "issues": [],
            }

        return review_data

    async def plan(
        self,
        plan_content: str,
        timeout_sec: int,
        work_dir: Path | None = None,
        context: str | None = None,
    ) -> dict:
        """Generate task DAG from plan using Claude Code CLI.

        Args:
            plan_content: Plan file content
            timeout_sec: Planning timeout
            work_dir: Working directory

        Returns:
            Plan dict with tasks, edges, topo_order, parallel_batches
        """
        context_block = f"\n## Plan Context\n{context}\n" if context else ""
        prompt = f"""Convert the following plan into a task dependency graph with enriched metadata.{context_block}

Important: Do NOT scan the repo or look for AGENTS.md. Use only the plan content below.

## Plan
{plan_content}

Return JSON with the key:
- tasks: list of task objects

Each task object must include:
- id: "task-1", "task-2", ...
- title
- description
- goal
- acceptance_criteria: list of strings
- allowed_paths: list of path prefixes
- validation_commands: object with optional overrides (tests, lint, format, uat)
- depends_on: list of task ids this task depends on (may be empty)
- suggested_claude_skills: list of strings
- suggested_mcp_servers: list of strings
- suggested_subagents: list of strings
- estimated_complexity: "low" | "medium" | "high" | "critical"

Rules:
- You MUST decompose large or multi-part plan items into bite-size tasks suitable for a swarm.
- A task should be small and single-scope (e.g., one screen, one endpoint, one component, one data layer change).
- Target tasks that a single agent can complete and validate quickly (roughly 1–2 hours, a few files).
- It is OK (expected) to output MORE tasks than listed in the plan.
- Explicitly encode dependencies so the DAG can parallelize safely.
- Prefer separating foundations (design system, routing, data models) from feature work.
- Ensure depends_on references ONLY the task ids you emit.
- Use ids task-1..task-N in order, with N = total tasks you produce.

Output ONLY the JSON, no other text."""

        manager = SubprocessManager(timeout_sec=timeout_sec)

        # Use a neutral directory (home or temp) to avoid repo access during planning
        # This prevents Claude from scanning repo files or using repo-based tools
        if work_dir is None:
            import tempfile
            work_dir = Path(tempfile.gettempdir())

        # For planning, use verbose output (required by --output-format=stream-json with --print)
        result = await manager.run(
            command=[
                self.cli_path,
                "--permission-mode", self.permission_mode,
                "--print",
                "--verbose",  # Required when using --output-format=stream-json with --print
                "--output-format", "stream-json",
                prompt,
            ],
            cwd=work_dir,
        )

        if not result["success"]:
            logger.error(f"Claude planning result: success={result['success']}, exit_code={result.get('exit_code')}, timed_out={result.get('timed_out')}, stuck={result.get('stuck')}")
            logger.error(f"Output (first 1000 chars): {result['output'][:1000]}")
            raise AgentError(f"Claude planning failed: {result['output']}")

        # Extract JSON from output
        output = result["output"]

        # Truncate excessive output to prevent memory issues
        max_output_size = 1024 * 1024  # 1MB max
        if len(output) > max_output_size:
            logger.warning(f"Output too large ({len(output)} bytes), truncating to {max_output_size} bytes")
            # Try to find the end of the JSON by looking for closing braces
            truncated = output[:max_output_size]
            # Find last occurrence of complete JSON object
            last_brace = truncated.rfind('}')
            if last_brace > 0:
                # Also check for matching opening brace
                matching_open = truncated.rfind('{', 0, last_brace)
                if matching_open >= 0:
                    output = truncated[:last_brace + 1]
                    logger.info(f"Truncated output to {len(output)} bytes (found JSON end)")
                else:
                    output = truncated[:max_output_size]
            else:
                output = truncated[:max_output_size]

        plan_data = self._extract_summary(output)

        if plan_data is None:
            # Enhanced error logging with raw output
            logger.error(f"Failed to extract JSON from Claude planning output")
            logger.error(f"Output length: {len(output)} characters")
            logger.error(f"Raw output (first 3000 chars):\n{output[:3000]}")

            # Try to save raw output for debugging
            try:
                debug_path = Path(".autopilot/plan") / "claude_parse_error_raw.txt"
                debug_path.parent.mkdir(parents=True, exist_ok=True)
                debug_path.write_text(output)
                logger.error(f"Raw output saved to: {debug_path}")
            except Exception as e:
                logger.error(f"Failed to save debug output: {e}")

            raise AgentError("Failed to extract JSON from Claude planning output")

        return plan_data

    async def generate_uat(
        self,
        task_content: str,
        diff: str,
        timeout_sec: int,
        work_dir: Path | None = None,
        context: str | None = None,
    ) -> str:
        """Generate UAT cases as executable Python pytest code using Claude Code CLI.

        Args:
            task_content: Task file content
            diff: Current git diff
            timeout_sec: Generation timeout
            work_dir: Working directory

        Returns:
            Generated UAT Python code (pytest-compatible)
        """
        context_block = f"\n## Task Context\n{context}\n" if context else ""
        prompt = f"""Generate User Acceptance Tests (UAT) as executable Python pytest code for the following task and implementation.{context_block}

Important: Do NOT scan the repo or look for AGENTS.md. Use only the task and diff below.

## Task
{task_content}

## Implementation Diff
{diff}

Generate comprehensive UAT test cases as Python pytest functions covering:
- Happy path scenarios
- Edge cases
- Error conditions
- Integration points
- User workflows

Format as executable Python code:
```python
import pytest
from pathlib import Path

class TestUATTask:
    \"\"\"UAT tests for this task.\"\"\"

    def test_happy_path(self):
        \"\"\"Test a happy-path scenario.\"\"\"
        assert True
```

IMPORTANT:
- Output ONLY valid Python code
- Include necessary imports
- Use pytest conventions (test_ prefix, assertions, fixtures)
- Make tests executable and independent
- Include docstrings explaining each test
- Use proper assertions (assert, pytest.raises)
- Do NOT use angle brackets like `<TaskName>` in identifiers; all names must be valid Python syntax.

Output ONLY the Python code, no markdown formatting, no explanations."""

        manager = SubprocessManager(timeout_sec=timeout_sec)
        result = await manager.run(
            command=[
                self.cli_path,
                "--permission-mode", self.permission_mode,
                "--print",
                "--verbose",
                "--output-format", "stream-json",
                "--include-partial-messages",
                prompt,
            ],
            cwd=work_dir or Path.cwd(),
        )

        if not result["success"]:
            raise AgentError(f"Claude UAT generation failed: {result['output']}")

        # Extract Python code from output
        output = result["output"]
        uat_code = self._extract_code(output)

        if uat_code is None:
            # Fallback: return a basic test
            return """import pytest

class TestUATTask:
    \"\"\"UAT tests for this task.\"\"\"

    def test_happy_path(self):
        \"\"\"Test a happy-path scenario.\"\"\"
        assert True
"""

        return uat_code

    def _extract_summary(self, output: str) -> dict | None:
        """Extract JSON summary from Claude output.

        Args:
            output: Claude CLI output (may be streaming JSON format)

        Returns:
            Parsed summary dict or None
        """
        # First, try to extract text from streaming JSON format
        # But only if the output is actually in streaming JSON format
        lines = output.split("\n")

        # Detect if we have streaming JSON format (check if majority of lines parse as JSON with known types)
        json_line_count = 0
        for line in lines:
            try:
                payload = json.loads(line)
                payload_type = payload.get("type", "")
                if payload_type in ("stream_event", "assistant", "error", "message"):
                    json_line_count += 1
            except (json.JSONDecodeError, Exception):
                pass

        is_streaming_json = json_line_count > len(lines) * 0.3  # At least 30% of lines are streaming JSON

        text_parts = []
        if is_streaming_json:
            # Process as streaming JSON
            for line in lines:
                try:
                    payload = json.loads(line)
                    payload_type = payload.get("type")
                    if payload_type == "stream_event":
                        event = payload.get("event", {})
                        if event.get("type") == "content_block_delta":
                            delta = event.get("delta", {})
                            if delta.get("type") == "text_delta":
                                text = delta.get("text", "")
                                if text:
                                    text_parts.append(text)
                    elif payload_type == "assistant":
                        message = payload.get("message", {})
                        contents = message.get("content", [])
                        if isinstance(contents, list):
                            for block in contents:
                                if block.get("type") == "text" and block.get("text"):
                                    text_parts.append(block["text"])
                except (json.JSONDecodeError, Exception):
                    # Skip unparseable lines in streaming JSON mode
                    pass
        else:
            # Treat as plain text - preserve all lines including newlines
            text_parts = lines

        # If we extracted text, use it; otherwise use raw output
        text_output = "\n".join(text_parts) if text_parts else output

        logger.debug(f"Extracted {len(text_parts)} text parts from {len(output.split(chr(10)))} output lines")
        logger.debug(f"Text output length: {len(text_output)} chars")

        # Try to parse as complete JSON first (for non-streaming format)
        try:
            parsed = json.loads(text_output.strip())
            if isinstance(parsed, dict) and "tasks" in parsed:
                logger.debug("Successfully parsed complete JSON output")
                return parsed
        except json.JSONDecodeError:
            pass  # Continue with other extraction methods

        # Look for JSON in code blocks or plain JSON
        try:
            lines = text_output.split("\n")
            in_code_block = False
            code_block_content = []
            json_start = -1

            for i, line in enumerate(lines):
                # Check for code fence markers
                if line.strip().startswith("```"):
                    if in_code_block:
                        # End of code block - try to parse
                        in_code_block = False
                        json_text = "\n".join(code_block_content)
                        try:
                            parsed = json.loads(json_text)
                            logger.debug(f"Successfully parsed JSON from code block at line {i}")
                            return parsed
                        except json.JSONDecodeError as e:
                            logger.debug(f"Failed to parse code block JSON: {e}")
                            pass  # Try next JSON block
                        code_block_content = []
                    else:
                        # Start of code block
                        in_code_block = True
                        # Check if this specifies json
                        if "json" in line.lower():
                            # Found ```json block
                            code_block_content = []
                        continue
                elif in_code_block:
                    code_block_content.append(line)
                else:
                    # Not in code block - look for plain JSON
                    stripped = line.strip()
                    if json_start == -1 and (stripped.startswith("{") or stripped.startswith("[")):
                        json_start = i
                        # Try to parse immediately as it might be a single-line JSON
                        try:
                            parsed = json.loads(stripped)
                            logger.debug(f"Successfully parsed single-line JSON at line {i}")
                            return parsed
                        except json.JSONDecodeError:
                            pass  # Not single-line JSON, continue accumulating
                    elif json_start >= 0:
                        # Try parsing accumulated JSON
                        json_text = "\n".join(lines[json_start : i + 1])
                        try:
                            parsed = json.loads(json_text)
                            logger.debug(f"Successfully parsed JSON from plain text at line {i}")
                            return parsed
                        except json.JSONDecodeError:
                            pass  # Continue looking

            # If we found JSON start, try to parse from there
            if json_start >= 0:
                json_text = "\n".join(lines[json_start:])
                try:
                    parsed = json.loads(json_text)
                    logger.debug(f"Successfully parsed JSON from end of output")
                    return parsed
                except json.JSONDecodeError as e:
                    logger.debug(f"Failed to parse final JSON: {e}")

            # Fallback: Try to fix common JSON issues and parse
            try:
                fixed_json = self._fix_malformed_json(text_output)
                if fixed_json:
                    parsed = json.loads(fixed_json)
                    logger.debug("Successfully parsed JSON after fixing common issues")
                    return parsed
            except Exception as e:
                logger.debug(f"Failed to parse fixed JSON: {e}")

        except (json.JSONDecodeError, Exception) as e:
            logger.debug(f"Failed to extract JSON summary: {e}")

        # Log output for debugging when extraction fails
        logger.debug(f"Failed to extract JSON from output. Output length: {len(output)}")
        logger.debug(f"Raw output (first 2000 chars):\n{output[:2000]}")
        logger.debug(f"Text output (first 2000 chars):\n{text_output[:2000]}")

        return None

    def _extract_code(self, output: str) -> str | None:
        """Extract Python code from Claude output.

        Args:
            output: Claude CLI output

        Returns:
            Extracted Python code string or None
        """
        try:
            # Look for Python code blocks
            lines = output.split("\n")
            in_code_block = False
            code_lines = []

            for line in lines:
                # Check for code fence markers
                if line.strip().startswith("```"):
                    if "python" in line.lower():
                        in_code_block = True
                        continue
                    elif in_code_block:
                        # End of code block
                        break
                elif in_code_block:
                    code_lines.append(line)
                elif line.strip().startswith("import ") or line.strip().startswith("from ") or line.strip().startswith("class ") or line.strip().startswith("def "):
                    # Started code without fence marker
                    code_lines.append(line)
                    in_code_block = True

            if code_lines:
                return "\n".join(code_lines)

        except Exception as e:
            logger.debug(f"Failed to extract code: {e}")

        return None

    def _fix_malformed_json(self, text: str) -> str | None:
        """Attempt to fix common JSON issues and return valid JSON string.

        Args:
            text: Potentially malformed JSON text

        Returns:
            Fixed JSON string or None if can't be fixed
        """
        import re

        try:
            # Try parsing as-is first
            json.loads(text)
            return text
        except json.JSONDecodeError:
            pass

        # Fix 1: Remove trailing commas
        # Handle both objects and arrays
        fixed = re.sub(r',(\s*[}\]])', r'\1', text)

        # Fix 2: Remove comments (both // and /* */ style)
        fixed = re.sub(r'//.*?$', '', fixed, flags=re.MULTILINE)
        fixed = re.sub(r'/\*.*?\*/', '', fixed, flags=re.DOTALL)

        # Fix 3: Ensure keys are quoted (unquoted keys)
        fixed = re.sub(r'([{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)(\s*:)', r'\1"\2"\3', fixed)

        # Fix 4: Remove single quotes and replace with double quotes (but careful with escaped quotes)
        # This is tricky, so we'll try it and if it fails, we revert
        try:
            # Replace single quotes with double quotes, but preserve escaped single quotes
            temp = fixed
            # First, protect escaped single quotes
            temp = temp.replace("\\'", "___SINGLE_QUOTE_ESC___")
            # Then replace single quotes with double quotes
            temp = temp.replace("'", '"')
            # Restore escaped single quotes
            temp = temp.replace("___SINGLE_QUOTE_ESC___", "\\'")
            fixed = temp
        except Exception:
            pass  # Keep original if this fails

        # Fix 5: Try to close incomplete JSON
        # Count braces and brackets
        open_braces = fixed.count('{')
        close_braces = fixed.count('}')
        open_brackets = fixed.count('[')
        close_brackets = fixed.count(']')

        # Add missing closing brackets/braces
        if close_braces < open_braces:
            fixed += '}' * (open_braces - close_braces)
        if close_brackets < open_brackets:
            fixed += ']' * (open_brackets - close_brackets)

        # Try to parse the fixed version
        try:
            json.loads(fixed)
            return fixed
        except json.JSONDecodeError:
            pass

        # Fix 6: Extract just the JSON object/array if mixed with text
        # Find the largest {...} or [...] block
        matches = []

        # Find all {...} blocks
        stack = []
        start = -1
        for i, char in enumerate(fixed):
            if char == '{':
                if not stack:
                    start = i
                stack.append('{')
            elif char == '}' and stack and stack[-1] == '{':
                stack.pop()
                if not stack and start >= 0:
                    matches.append((start, i + 1, fixed[start:i+1]))

        # Find all [...] blocks
        stack = []
        start = -1
        for i, char in enumerate(fixed):
            if char == '[':
                if not stack:
                    start = i
                stack.append('[')
            elif char == ']' and stack and stack[-1] == '[':
                stack.pop()
                if not stack and start >= 0:
                    matches.append((start, i + 1, fixed[start:i+1]))

        # Try each match, largest first
        for _, _, json_str in sorted(matches, key=lambda x: x[1]-x[0], reverse=True):
            if len(json_str) > 100:  # Only consider substantial JSON
                try:
                    json.loads(json_str)
                    return json_str
                except json.JSONDecodeError:
                    continue

        return None

    def _wrap_prompt(self, prompt: str) -> str:
        """Wrap prompt with diff-only instruction."""
        return (
            "You are running in non-interactive mode. "
            "Output ONLY a unified git diff that can be applied with `git apply`. "
            "Do NOT include commentary, explanations, or code fences. "
            "Do NOT run shell commands, install dependencies, or attempt repo-wide fixes. "
            "ONLY make changes required for the specific task and ONLY within the allowed paths "
            "mentioned in the task context; ignore unrelated errors elsewhere in the repo. "
            "Your output must start with 'diff --git' or '--- a/'. "
            "If no changes are needed, output exactly: NO_CHANGES.\n\n"
            f"{prompt}"
        )

    def _extract_diff(self, output: str) -> str:
        """Extract a unified diff from Claude output."""

        def _looks_like_diff(text: str) -> bool:
            return "diff --git" in text or ("\n--- " in f"\n{text}" and "\n+++ " in f"\n{text}")

        def _looks_like_stray_commentary(line: str) -> bool:
            """Best-effort filter for model commentary accidentally emitted inside a diff."""
            s = line.strip()
            if not s:
                return False

            # Keep common code-ish prefixes (even if the leading diff +/- is missing).
            for prefix in (
                "#",
                "import ",
                "from ",
                "def ",
                "class ",
                "return ",
                "if ",
                "elif ",
                "else",
                "for ",
                "while ",
                "with ",
                "try",
                "except",
                "raise",
                "assert",
            ):
                if s.startswith(prefix):
                    return False

            # Common English sentences produced by the model; better to drop than inject into code.
            if s.startswith(
                (
                    "Let me",
                    "I'll",
                    "I need",
                    "I will",
                    "Now ",
                    "Based on",
                )
            ):
                return True

            # Heuristic: sentence-like capitalization + spaces + punctuation.
            if s[0].isupper() and " " in s and s.endswith((".", ":", "!", "?")):
                return True

            return False

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

        # Fallback: extract from first diff-like line and filter out stray commentary.
        lines = output.splitlines()
        start_idx = None
        for idx, line in enumerate(lines):
            if line.startswith("diff --git"):
                start_idx = idx
                break
            if line.startswith("--- "):
                if idx + 1 < len(lines) and lines[idx + 1].startswith("+++ "):
                    start_idx = idx
                    break

        if start_idx is None:
            return ""

        allowed_meta_prefixes = (
            "index ",
            "new file mode",
            "deleted file mode",
            "similarity index",
            "rename from",
            "rename to",
            "old mode",
            "new mode",
            "Binary files",
            "GIT binary patch",
        )

        keep: list[str] = []
        in_hunk = False
        for line in lines[start_idx:]:
            if line.startswith("diff --git"):
                in_hunk = False
                keep.append(line)
                continue
            if line.startswith(allowed_meta_prefixes):
                keep.append(line)
                continue
            if line.startswith(("--- ", "+++ ")):
                keep.append(line)
                continue
            if line.startswith("@@"):
                in_hunk = True
                keep.append(line)
                continue
            if line.startswith("\\ No newline"):
                keep.append(line)
                continue

            if in_hunk:
                if line.startswith(("+", "-", " ")):
                    keep.append(line)
                    continue
                if line == "":
                    # A blank line must still carry a diff prefix; treat as adding a blank line.
                    keep.append("+")
                    continue
                if _looks_like_stray_commentary(line):
                    continue
                # Assume a missing prefix; treat as an addition to keep the patch parseable.
                keep.append(f"+{line}")
            else:
                # Skip any stray non-diff lines between diff blocks.
                continue

        return "\n".join(keep).strip()

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
        pre_entries: dict[str, str] = {}
        try:
            manager = SubprocessManager(timeout_sec=30)
            pre = await manager.run(["git", "status", "--porcelain"], cwd=target_dir)
            pre_entries = self._porcelain_non_artifact_entries(pre.get("output", "") or "")
        except Exception:
            pre_entries = {}

        patch_path = patch_dir / f"claude_{int(time.time())}.diff"
        patch_text = self._sanitize_diff(diff)
        if not patch_text.endswith("\n"):
            patch_text = f"{patch_text}\n"
        patch_path.write_text(patch_text, encoding="utf-8")

        manager = SubprocessManager(timeout_sec=60)
        apply_attempts = [
            ["git", "apply", "--whitespace=nowarn", str(patch_path)],
            # Some LLM diffs have incorrect hunk line counts; --recount fixes many of these.
            ["git", "apply", "--recount", "--whitespace=nowarn", str(patch_path)],
            # Fallback to 3-way apply for patch context mismatches
            ["git", "apply", "--3way", "--whitespace=nowarn", str(patch_path)],
            ["git", "apply", "--3way", "--recount", "--whitespace=nowarn", str(patch_path)],
        ]

        last = None
        for cmd in apply_attempts:
            last = await manager.run(cmd, cwd=target_dir)
            if last["success"]:
                return

        # If Claude applied edits directly, accept only if the non-artifact working tree changed
        # relative to the pre-apply snapshot. This prevents false-positive successes when only
        # `.autopilot/patches/*` was created.
        status = await manager.run(
            ["git", "status", "--porcelain"],
            cwd=target_dir,
        )
        post_entries = self._porcelain_non_artifact_entries(status.get("output", "") or "")
        if post_entries and post_entries != pre_entries:
            logger.warning("Claude diff failed to apply but working tree has changes; continuing")
            return

        raise AgentError(f"Failed to apply Claude diff: {last['output'] if last else ''}")

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
