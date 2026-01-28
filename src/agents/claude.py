"""Claude Code CLI agent wrapper."""

import json
import logging
from pathlib import Path
from typing import Optional

from .base import BaseAgent, AgentError
from ..utils.subprocess import SubprocessManager, SubprocessError

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

    async def execute(
        self,
        prompt: str,
        timeout_sec: int,
        work_dir: Optional[Path] = None,
    ) -> dict:
        """Execute build with Claude Code CLI.

        Args:
            prompt: Build prompt/task
            timeout_sec: Execution timeout
            work_dir: Working directory

        Returns:
            Result dict
        """
        # Build command with non-interactive flag
        command = [self.cli_path, "--non-interactive", prompt]

        retries = 0
        last_error = None

        while retries <= self.max_retries:
            try:
                manager = SubprocessManager(timeout_sec=timeout_sec)
                result = await manager.run(command, cwd=work_dir)

                if result["success"]:
                    # Try to extract JSON summary
                    summary = self._extract_summary(result["output"])
                    return {
                        **result,
                        "summary": summary,
                    }
                else:
                    # Build failed
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
        work_dir: Optional[Path] = None,
    ) -> dict:
        """Claude doesn't support review mode - use Codex agent."""
        raise NotImplementedError("Use Codex agent for review")

    async def plan(
        self,
        plan_content: str,
        timeout_sec: int,
        work_dir: Optional[Path] = None,
    ) -> dict:
        """Claude doesn't support planning mode - use Codex agent."""
        raise NotImplementedError("Use Codex agent for planning")

    async def generate_uat(
        self,
        task_content: str,
        diff: str,
        timeout_sec: int,
        work_dir: Optional[Path] = None,
    ) -> str:
        """Claude doesn't support UAT generation - use Codex agent."""
        raise NotImplementedError("Use Codex agent for UAT generation")

    def _extract_summary(self, output: str) -> Optional[dict]:
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
