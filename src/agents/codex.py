"""Codex CLI / OpenAI API wrapper for review, plan, and UAT generation."""

import json
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path

from ..utils.subprocess import SubprocessError, SubprocessManager
from .base import AgentError, BaseAgent

logger = logging.getLogger(__name__)


class CodexAgent(BaseAgent):
    """Codex/OpenAI agent for review, planning, and UAT generation."""

    def __init__(self, config: dict):
        """Initialize Codex agent.

        Args:
            config: Agent config with mode (codex_cli or openai_api), model, api_key_env
        """
        super().__init__(config)
        self.mode = config.get("mode", "codex_cli")
        cfg_model = config.get("model")
        if self.mode == "codex_cli":
            # Codex CLI defaults to whatever is in ~/.codex/config.toml; Autopilot should be
            # deterministic and not depend on the user's interactive defaults.
            self.model = cfg_model or "gpt-5.2-codex"
            self.model_reasoning_effort = config.get("model_reasoning_effort") or "medium"
        else:
            self.model = cfg_model or os.environ.get("OPENAI_MODEL")
            self.model_reasoning_effort = None
        # Some configs specify api_key_env=null; treat that as unset.
        self.api_key_env = config.get("api_key_env") or "OPENAI_API_KEY"
        self.json_schema_path = config.get("json_schema_path")
        self.disable_mcp = config.get("disable_mcp", True)
        self.codex_home = config.get("codex_home")
        self.codex_overrides = config.get("codex_overrides", [])
        self._isolated_codex_home: Path | None = None
        self.max_retries = config.get("max_retries", 1)

    async def execute(
        self,
        prompt: str,
        timeout_sec: int,
        work_dir: Path | None = None,
    ) -> dict:
        """Execute build using Codex/OpenAI.

        This is used for code generation steps. The agent must output either:
        - a unified git diff (preferred), or
        - the literal marker: NO_CHANGES
        """
        retries = 0
        last_error: Exception | None = None

        while retries <= self.max_retries:
            try:
                wrapped_prompt = self._wrap_build_prompt(prompt)
                if self.mode == "codex_cli":
                    manager = SubprocessManager(timeout_sec=timeout_sec)
                    result = await manager.run(
                        self._build_codex_command(wrapped_prompt),
                        cwd=work_dir,
                        env=self._get_codex_env(),
                    )
                    output_text = result["output"]
                else:
                    output_text = await self._build_openai(wrapped_prompt, timeout_sec)
                    result = {
                        "success": True,
                        "output": output_text,
                        "exit_code": 0,
                        "timed_out": False,
                        "stuck": False,
                    }

                if not output_text.strip():
                    if result.get("success"):
                        return {**result, "success": True, "summary": None}
                    raise AgentError("Build produced no output.")

                diff = self._extract_diff(output_text)
                if diff:
                    try:
                        await self._apply_diff(diff, work_dir)
                    except AgentError as e:
                        # Some codex invocations may have edited files directly (rare). If so,
                        # accept those changes rather than failing on an apply error.
                        status_mgr = SubprocessManager(timeout_sec=30)
                        try:
                            status = await status_mgr.run(
                                ["git", "status", "--porcelain"],
                                cwd=work_dir,
                            )
                            if status.get("output", "").strip():
                                logger.warning(
                                    "Codex diff failed to apply but working tree has changes; continuing"
                                )
                                return {**result, "success": True, "summary": None}
                        except Exception:
                            pass

                        # Retry with feedback so the model re-diffs against current state.
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
                    raise AgentError(
                        "Build output did not include a unified diff or NO_CHANGES marker."
                    )

                return {**result, "success": True, "summary": None}

            except SubprocessError as e:
                last_error = e
                if e.timed_out or e.stuck:
                    retries += 1
                    logger.warning(
                        "Build timed out or stuck (retry %s/%s)", retries, self.max_retries
                    )
                    continue
                raise AgentError(f"Build subprocess error: {e}")
            except Exception as e:
                last_error = e
                break

        raise AgentError(f"Build failed after {retries} retries: {last_error}")

    def _wrap_build_prompt(self, prompt: str) -> str:
        """Wrap prompt with diff-only instruction."""
        return (
            "You are running in non-interactive mode. "
            "Output ONLY a unified git diff that can be applied with `git apply`. "
            "Do NOT include commentary, explanations, or code fences. "
            "Your output must start with 'diff --git' or '--- a/'. "
            "If no changes are needed, output exactly: NO_CHANGES.\n\n"
            f"{prompt}"
        )

    async def _build_openai(self, prompt: str, timeout_sec: int) -> str:
        """Build using OpenAI API (chat.completions)."""
        try:
            import openai

            api_key = os.environ.get(self.api_key_env)
            if not api_key:
                raise AgentError(f"API key not found: {self.api_key_env}")
            if not self.model:
                raise AgentError("Model not configured. Set builder.model or OPENAI_MODEL.")

            client = openai.OpenAI(api_key=api_key)
            response = client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                timeout=timeout_sec,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            raise AgentError(f"OpenAI build error: {e}")

    def _extract_diff(self, output: str) -> str:
        """Extract a unified diff from output (handles preambles/fences)."""

        def _looks_like_diff(text: str) -> bool:
            return "diff --git" in text or ("\n--- " in f"\n{text}" and "\n+++ " in f"\n{text}")

        def _looks_like_stray_commentary(line: str) -> bool:
            s = line.strip()
            if not s:
                return False

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

            if s[0].isupper() and " " in s and s.endswith((".", ":", "!", "?")):
                return True

            return False

        # Prefer fenced diff blocks if present.
        if "```" in output:
            sections = output.split("```")
            diff_blocks = []
            for i in range(1, len(sections), 2):
                block = sections[i].strip()
                if _looks_like_diff(block):
                    diff_blocks.append(block)
            if diff_blocks:
                return diff_blocks[-1]

        # Fallback: extract from first diff-like line and filter stray commentary.
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
                    keep.append("+")
                    continue
                if _looks_like_stray_commentary(line):
                    continue
                keep.append(f"+{line}")
            else:
                continue

        return "\n".join(keep).strip()

    async def _apply_diff(self, diff: str, work_dir: Path | None) -> None:
        """Apply diff with git apply in working directory."""
        target_dir = work_dir or Path.cwd()
        patch_dir = target_dir / ".autopilot" / "patches"
        patch_dir.mkdir(parents=True, exist_ok=True)
        patch_path = patch_dir / f"codex_{int(time.time())}.diff"
        patch_text = self._sanitize_diff(diff)
        if not patch_text.endswith("\n"):
            patch_text = f"{patch_text}\n"
        patch_path.write_text(patch_text, encoding="utf-8")

        manager = SubprocessManager(timeout_sec=60)
        apply_attempts = [
            ["git", "apply", "--whitespace=nowarn", str(patch_path)],
            # Some LLM diffs have incorrect hunk line counts; --recount fixes many of these.
            ["git", "apply", "--recount", "--whitespace=nowarn", str(patch_path)],
            ["git", "apply", "--3way", "--whitespace=nowarn", str(patch_path)],
            ["git", "apply", "--3way", "--recount", "--whitespace=nowarn", str(patch_path)],
        ]

        last = None
        for cmd in apply_attempts:
            last = await manager.run(cmd, cwd=target_dir)
            if last["success"]:
                return

        raise AgentError(f"Failed to apply Codex diff: {last['output'] if last else ''}")

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

    async def review(
        self,
        diff: str,
        validation_output: str,
        timeout_sec: int,
        work_dir: Path | None = None,
        context: str | None = None,
    ) -> dict:
        """Review code changes.

        Args:
            diff: Git diff of changes
            validation_output: Validation output
            timeout_sec: Review timeout
            work_dir: Working directory

        Returns:
            Review dict with verdict, feedback, issues
        """
        prompt = self._build_review_prompt(diff, validation_output, context=context)

        if self.mode == "codex_cli":
            return await self._review_codex(prompt, timeout_sec, work_dir)
        else:
            return await self._review_openai(prompt, timeout_sec)

    async def plan(
        self,
        plan_content: str,
        timeout_sec: int,
        work_dir: Path | None = None,
        context: str | None = None,
    ) -> dict:
        """Generate task DAG from plan.

        Args:
            plan_content: Plan file content
            timeout_sec: Planning timeout
            work_dir: Working directory

        Returns:
            Plan dict with tasks, edges, topo_order, parallel_batches
        """
        prompt = self._build_plan_prompt(plan_content, context=context)

        if self.mode == "codex_cli":
            return await self._plan_codex(prompt, timeout_sec, work_dir)
        else:
            return await self._plan_openai(prompt, timeout_sec)

    async def generate_uat(
        self,
        task_content: str,
        diff: str,
        timeout_sec: int,
        work_dir: Path | None = None,
        context: str | None = None,
    ) -> str:
        """Generate UAT cases as executable Python pytest code.

        Args:
            task_content: Task file content
            diff: Current git diff
            timeout_sec: Generation timeout
            work_dir: Working directory

        Returns:
            Generated UAT Python code (pytest-compatible)
        """
        prompt = self._build_uat_prompt(task_content, diff, context=context)

        if self.mode == "codex_cli":
            return await self._uat_codex(prompt, timeout_sec, work_dir)
        else:
            return await self._uat_openai(prompt, timeout_sec)

    def _build_review_prompt(
        self,
        diff: str,
        validation_output: str,
        context: str | None = None,
    ) -> str:
        """Build review prompt."""
        context_block = f"\n## Task Context\n{context}\n" if context else ""
        return f"""Review the following code changes and validation output.{context_block}

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

    def _build_plan_prompt(self, plan_content: str, context: str | None = None) -> str:
        """Build planning prompt."""
        context_block = f"\n## Plan Context\n{context}\n" if context else ""
        return f"""Convert the following plan into a task dependency graph with enriched metadata.{context_block}

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

    def _build_uat_prompt(
        self,
        task_content: str,
        diff: str,
        context: str | None = None,
    ) -> str:
        """Build UAT generation prompt."""
        context_block = f"\n## Task Context\n{context}\n" if context else ""
        return f"""Generate User Acceptance Tests (UAT) as executable Python pytest code for the following task and implementation.{context_block}

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

    async def _review_codex(self, prompt: str, timeout_sec: int, work_dir: Path | None) -> dict:
        """Review using Codex CLI."""
        try:
            # Assuming codex CLI exists
            manager = SubprocessManager(timeout_sec=timeout_sec)
            command = self._build_codex_command(
                prompt=prompt,
                schema_path=self.json_schema_path,
            )
            result = await manager.run(command, cwd=work_dir, env=self._get_codex_env())
            try:
                return self._parse_review_json(result["output"])
            except AgentError:
                if result["success"]:
                    raise
                raise AgentError(f"Codex review failed: {result['output']}")

        except SubprocessError as e:
            raise AgentError(f"Codex review error: {e}")

    async def _review_openai(self, prompt: str, timeout_sec: int) -> dict:
        """Review using OpenAI API."""
        try:
            import openai

            api_key = os.environ.get(self.api_key_env)
            if not api_key:
                raise AgentError(f"API key not found: {self.api_key_env}")
            if not self.model:
                raise AgentError("Model not configured. Set reviewer.model or OPENAI_MODEL.")

            client = openai.OpenAI(api_key=api_key)

            response = client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                timeout=timeout_sec,
            )

            output = response.choices[0].message.content
            return self._parse_review_json(output)

        except Exception as e:
            raise AgentError(f"OpenAI review error: {e}")

    async def _plan_codex(self, prompt: str, timeout_sec: int, work_dir: Path | None) -> dict:
        """Plan using Codex CLI."""
        try:
            manager = SubprocessManager(timeout_sec=timeout_sec)
            result = await manager.run(
                self._build_codex_command(
                    prompt=prompt,
                    schema_path=self.json_schema_path,
                ),
                cwd=work_dir,
                env=self._get_codex_env(),
            )
            try:
                return self._parse_plan_json(result["output"])
            except AgentError:
                if result["success"]:
                    raise
                raise AgentError(f"Codex plan failed: {result['output']}")

        except SubprocessError as e:
            raise AgentError(f"Codex plan error: {e}")

    async def _plan_openai(self, prompt: str, timeout_sec: int) -> dict:
        """Plan using OpenAI API."""
        try:
            import openai

            api_key = os.environ.get(self.api_key_env)
            if not api_key:
                raise AgentError(f"API key not found: {self.api_key_env}")
            if not self.model:
                raise AgentError("Model not configured. Set planner.model or OPENAI_MODEL.")

            client = openai.OpenAI(api_key=api_key)

            response = client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                timeout=timeout_sec,
            )

            output = response.choices[0].message.content
            return self._parse_plan_json(output)

        except Exception as e:
            raise AgentError(f"OpenAI plan error: {e}")

    async def _uat_codex(self, prompt: str, timeout_sec: int, work_dir: Path | None) -> str:
        """Generate UAT using Codex CLI."""
        try:
            manager = SubprocessManager(timeout_sec=timeout_sec)
            result = await manager.run(
                self._build_codex_command(prompt),
                cwd=work_dir,
                env=self._get_codex_env(),
            )
            if result["success"]:
                return result["output"]
            if result["output"].strip():
                return result["output"]
            raise AgentError(f"Codex UAT generation failed: {result['output']}")

        except SubprocessError as e:
            raise AgentError(f"Codex UAT error: {e}")

    async def _uat_openai(self, prompt: str, timeout_sec: int) -> str:
        """Generate UAT using OpenAI API."""
        try:
            import openai

            api_key = os.environ.get(self.api_key_env)
            if not api_key:
                raise AgentError(f"API key not found: {self.api_key_env}")
            if not self.model:
                raise AgentError("Model not configured. Set reviewer.model or OPENAI_MODEL.")

            client = openai.OpenAI(api_key=api_key)

            response = client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                timeout=timeout_sec,
            )

            return response.choices[0].message.content

        except Exception as e:
            raise AgentError(f"OpenAI UAT error: {e}")

    def _parse_review_json(self, output: str) -> dict:
        """Parse review JSON from output."""
        try:
            decoder = json.JSONDecoder()
            candidates: list[dict] = []
            idx = output.find("{")
            while idx >= 0:
                try:
                    parsed, end = decoder.raw_decode(output[idx:])
                    if isinstance(parsed, dict):
                        candidates.append(parsed)
                    idx = output.find("{", idx + end)
                except json.JSONDecodeError:
                    idx = output.find("{", idx + 1)

            # Prefer the most recent dict that looks like a review response.
            for candidate in reversed(candidates):
                if {
                    "verdict",
                    "feedback",
                    "issues",
                }.issubset(candidate.keys()):
                    return candidate

            # Fallback to any parsed dict.
            if candidates:
                return candidates[-1]

        except json.JSONDecodeError as e:
            raise AgentError(f"Failed to parse review JSON: {e}")

        raise AgentError("No JSON found in review output")

    def _build_codex_command(self, prompt: str, schema_path: str | None = None) -> list[str]:
        """Build codex exec command with optional config overrides."""
        command = ["codex", "exec"]
        if self.model:
            command.extend(["-m", self.model])
        if schema_path:
            resolved = self._resolve_schema_path(schema_path)
            if resolved:
                command.extend(["--output-schema", str(resolved)])
            else:
                logger.warning("Schema file not found: %s (running without schema)", schema_path)

        overrides = list(self.codex_overrides)
        if self.model_reasoning_effort and not any(
            o.startswith("model_reasoning_effort") for o in overrides
        ):
            overrides.append(f'model_reasoning_effort="{self.model_reasoning_effort}"')

        for override in overrides:
            command.extend(["-c", override])

        command.append(prompt)
        return command

    @staticmethod
    def _resolve_schema_path(schema_path: str) -> Path | None:
        """Resolve schema path relative to repo or package."""
        raw_path = Path(schema_path).expanduser()
        if raw_path.is_absolute() and raw_path.exists():
            return raw_path

        # Try relative to current working directory.
        cwd_path = Path.cwd() / raw_path
        if cwd_path.exists():
            return cwd_path

        # Try relative to package root (repo).
        pkg_root = Path(__file__).resolve().parents[2]
        pkg_path = pkg_root / raw_path
        if pkg_path.exists():
            return pkg_path

        return None

    def _get_codex_env(self) -> dict[str, str] | None:
        """Return environment overrides for Codex CLI."""
        if not self.disable_mcp:
            return None

        if self._isolated_codex_home is None:
            self._isolated_codex_home = self._create_isolated_codex_home()

        env = os.environ.copy()
        env["HOME"] = str(self._isolated_codex_home)
        return env

    def _resolve_isolated_codex_home(self) -> Path:
        """Resolve a stable Codex home to avoid repeated temp copies."""
        if self.codex_home:
            return Path(self.codex_home).expanduser()

        env_home = os.environ.get("AUTOPILOT_CODEX_HOME")
        if env_home:
            return Path(env_home).expanduser()

        # Prefer a repo-local location if .autopilot is present.
        cwd = Path.cwd()
        for base in [cwd] + list(cwd.parents):
            if (base / ".autopilot").exists():
                return base / ".autopilot" / "codex-home"

        # Fallback to a stable temp location.
        return Path(tempfile.gettempdir()) / "autopilot-codex-home"

    def _create_isolated_codex_home(self) -> Path:
        """Create an isolated Codex home without MCP servers configured."""
        target_home = self._resolve_isolated_codex_home()
        target_codex = target_home / ".codex"

        target_codex.mkdir(parents=True, exist_ok=True)

        # Copy only the minimum required Codex state (auth + config). Copying the entire
        # ~/.codex directory can be extremely large (e.g., ~/.codex/tmp) and can exhaust disk.
        source_codex = Path.home() / ".codex"
        source_auth = source_codex / "auth.json"
        target_auth = target_codex / "auth.json"
        if source_auth.exists() and not target_auth.exists():
            try:
                shutil.copy2(source_auth, target_auth)
            except Exception as e:
                logger.warning("Failed to copy Codex auth.json into isolated home: %s", e)

        config_path = target_codex / "config.toml"
        if not config_path.exists():
            # Minimal config; we pass model/effort overrides on the CLI too.
            config_path.write_text(
                "\n".join(
                    [
                        f'model = "{self.model}"',
                        f'model_reasoning_effort = "{self.model_reasoning_effort}"',
                        "[notice]",
                        "hide_full_access_warning = true",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

        # Always ensure MCP server blocks are stripped (idempotent). This avoids stale configs
        # if the isolated home was created by an older Autopilot version.
        try:
            config_text = config_path.read_text(encoding="utf-8")
            config_path.write_text(self._strip_mcp_sections(config_text), encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to sanitize Codex config.toml in isolated home: %s", e)

        return target_home

    @staticmethod
    def _strip_mcp_sections(config_text: str) -> str:
        """Remove MCP server blocks from Codex config."""
        lines = config_text.splitlines()
        stripped: list[str] = []
        skip = False

        for line in lines:
            if line.startswith("[mcp_servers"):
                skip = True
                continue
            if skip and line.startswith("[") and not line.startswith("[mcp_servers"):
                skip = False
            if skip:
                continue
            stripped.append(line)

        return "\n".join(stripped) + ("\n" if config_text.endswith("\n") else "")

    def _parse_plan_json(self, output: str) -> dict:
        """Parse plan JSON from output."""
        try:
            decoder = json.JSONDecoder()
            candidates: list[dict] = []
            idx = output.find("{")
            while idx >= 0:
                try:
                    parsed, end = decoder.raw_decode(output[idx:])
                    if isinstance(parsed, dict):
                        candidates.append(parsed)
                    idx = output.find("{", idx + end)
                except json.JSONDecodeError:
                    idx = output.find("{", idx + 1)

            # Prefer the most recent dict that includes tasks with content
            for candidate in reversed(candidates):
                tasks = candidate.get("tasks")
                if isinstance(tasks, list) and tasks:
                    return candidate

            # Fallback to any dict that includes tasks (even empty)
            for candidate in reversed(candidates):
                if "tasks" in candidate:
                    return candidate

        except json.JSONDecodeError as e:
            raise AgentError(f"Failed to parse plan JSON: {e}")

        raise AgentError("No JSON found in plan output")
