"""Codex CLI / OpenAI API wrapper for review, plan, and UAT generation."""

import json
import logging
import os
from pathlib import Path
from typing import Optional

from .base import BaseAgent, AgentError
from ..utils.subprocess import SubprocessManager, SubprocessError

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
        self.model = config.get("model") or os.environ.get("OPENAI_MODEL")
        self.api_key_env = config.get("api_key_env", "OPENAI_API_KEY")
        self.json_schema_path = config.get("json_schema_path")
        self.disable_mcp = config.get("disable_mcp", True)
        self.codex_overrides = config.get("codex_overrides", [])

    async def execute(
        self,
        prompt: str,
        timeout_sec: int,
        work_dir: Optional[Path] = None,
    ) -> dict:
        """Codex doesn't support build mode - use Claude agent."""
        raise NotImplementedError("Use Claude agent for build")

    async def review(
        self,
        diff: str,
        validation_output: str,
        timeout_sec: int,
        work_dir: Optional[Path] = None,
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
        prompt = self._build_review_prompt(diff, validation_output)

        if self.mode == "codex_cli":
            return await self._review_codex(prompt, timeout_sec, work_dir)
        else:
            return await self._review_openai(prompt, timeout_sec)

    async def plan(
        self,
        plan_content: str,
        timeout_sec: int,
        work_dir: Optional[Path] = None,
    ) -> dict:
        """Generate task DAG from plan.

        Args:
            plan_content: Plan file content
            timeout_sec: Planning timeout
            work_dir: Working directory

        Returns:
            Plan dict with tasks, edges, topo_order, parallel_batches
        """
        prompt = self._build_plan_prompt(plan_content)

        if self.mode == "codex_cli":
            return await self._plan_codex(prompt, timeout_sec, work_dir)
        else:
            return await self._plan_openai(prompt, timeout_sec)

    async def generate_uat(
        self,
        task_content: str,
        diff: str,
        timeout_sec: int,
        work_dir: Optional[Path] = None,
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
        prompt = self._build_uat_prompt(task_content, diff)

        if self.mode == "codex_cli":
            return await self._uat_codex(prompt, timeout_sec, work_dir)
        else:
            return await self._uat_openai(prompt, timeout_sec)

    def _build_review_prompt(self, diff: str, validation_output: str) -> str:
        """Build review prompt."""
        return f"""Review the following code changes and validation output.

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

    def _build_plan_prompt(self, plan_content: str) -> str:
        """Build planning prompt."""
        return f"""Convert the following plan into a task dependency graph with enriched metadata.

Important: Do NOT scan the repo or look for AGENTS.md. Use only the plan content below.

## Plan
{plan_content}

Return JSON with keys:
- tasks: list of task objects
- edges: list of [from, to] task id pairs
- topo_order: list of task ids
- parallel_batches: list of lists of task ids

Each task object must include:
- id: "task-1", "task-2", ...
- title
- description
- goal
- acceptance_criteria: list of strings
- allowed_paths: list of path prefixes
- validation_commands: object with optional overrides (tests, lint, format, uat)
- depends_on: list of task ids
- dependencies: list of task ids
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
- Ensure edges/topo_order/parallel_batches reference ONLY the task ids you emit.
- Use ids task-1..task-N in order, with N = total tasks you produce.

Output ONLY the JSON, no other text."""

    def _build_uat_prompt(self, task_content: str, diff: str) -> str:
        """Build UAT generation prompt."""
        return f"""Generate User Acceptance Tests (UAT) as executable Python pytest code for the following task and implementation.

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
from your_module import your_functions

class TestUAT<TaskName>:
    \"\"\"UAT tests for <Task Name>.\"\"\"

    def test_happy_path_scenario_1(self):
        \"\"\"Test <scenario description>.\"\"\"
        # Given
        # Preconditions setup

        # When
        # Actions to test

        # Then
        # Expected outcomes
        assert result == expected

    def test_edge_case_1(self):
        \"\"\"Test <edge case description>.\"\"\"
        # Test implementation

    def test_error_condition_1(self):
        \"\"\"Test <error condition description>.\"\"\"
        # Test with pytest.raises for expected errors
        with pytest.raises(ValueError):
            # Code that should raise ValueError
            pass
```

IMPORTANT:
- Output ONLY valid Python code
- Include necessary imports
- Use pytest conventions (test_ prefix, assertions, fixtures)
- Make tests executable and independent
- Include docstrings explaining each test
- Use proper assertions (assert, pytest.raises)

Output ONLY the Python code, no markdown formatting, no explanations."""

    async def _review_codex(self, prompt: str, timeout_sec: int, work_dir: Optional[Path]) -> dict:
        """Review using Codex CLI."""
        try:
            # Assuming codex CLI exists
            manager = SubprocessManager(timeout_sec=timeout_sec)
            command = self._build_codex_command(
                prompt=prompt,
                schema_path=self.json_schema_path,
            )
            result = await manager.run(command, cwd=work_dir)
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

    async def _plan_codex(self, prompt: str, timeout_sec: int, work_dir: Optional[Path]) -> dict:
        """Plan using Codex CLI."""
        try:
            manager = SubprocessManager(timeout_sec=timeout_sec)
            result = await manager.run(self._build_codex_command(prompt), cwd=work_dir)
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

    async def _uat_codex(self, prompt: str, timeout_sec: int, work_dir: Optional[Path]) -> str:
        """Generate UAT using Codex CLI."""
        try:
            manager = SubprocessManager(timeout_sec=timeout_sec)
            result = await manager.run(self._build_codex_command(prompt), cwd=work_dir)
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
            json_start = output.find("{")
            if json_start >= 0:
                decoder = json.JSONDecoder()
                parsed, _ = decoder.raw_decode(output[json_start:])
                return parsed

        except json.JSONDecodeError as e:
            raise AgentError(f"Failed to parse review JSON: {e}")

        raise AgentError("No JSON found in review output")

    def _build_codex_command(self, prompt: str, schema_path: Optional[str] = None) -> list[str]:
        """Build codex exec command with optional config overrides."""
        command = ["codex", "exec"]
        if schema_path:
            command.extend(["--output-schema", schema_path])

        overrides = list(self.codex_overrides)
        if self.disable_mcp and not any(o.startswith("mcp_servers") for o in overrides):
            overrides.append("mcp_servers={}")

        for override in overrides:
            command.extend(["-c", override])

        command.append(prompt)
        return command

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
