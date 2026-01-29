"""Base agent interface."""

from abc import ABC, abstractmethod
from pathlib import Path


class AgentError(Exception):
    """Agent execution error."""

    pass


class BaseAgent(ABC):
    """Base agent interface."""

    def __init__(self, config: dict):
        """Initialize agent.

        Args:
            config: Agent configuration dict
        """
        self.config = config

    @abstractmethod
    async def execute(
        self,
        prompt: str,
        timeout_sec: int,
        work_dir: Path | None = None,
    ) -> dict:
        """Execute agent with prompt.

        Args:
            prompt: Prompt to send to agent
            timeout_sec: Execution timeout
            work_dir: Working directory for execution

        Returns:
            Result dict with keys:
                - success: bool
                - output: str (stdout/stderr combined)
                - exit_code: int
                - timed_out: bool
                - summary: Optional[dict] (parsed summary if available)

        Raises:
            AgentError: On execution failure
        """
        pass

    @abstractmethod
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
            validation_output: Validation/lint/test output
            timeout_sec: Review timeout
            work_dir: Working directory

        Returns:
            Review dict with keys:
                - verdict: str (approve/request_changes)
                - feedback: str
                - issues: list[str]
        """
        pass

    @abstractmethod
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
            Plan dict with keys:
                - tasks: list[dict] (task definitions)
                - edges: list[tuple] (dependency edges)
                - topo_order: list[str] (topological sort)
                - parallel_batches: list[list[str]] (parallelizable groups)
        """
        pass

    @abstractmethod
    async def generate_uat(
        self,
        task_content: str,
        diff: str,
        timeout_sec: int,
        work_dir: Path | None = None,
        context: str | None = None,
    ) -> str:
        """Generate UAT cases.

        Args:
            task_content: Task file content
            diff: Current git diff
            timeout_sec: Generation timeout
            work_dir: Working directory

        Returns:
            Generated UAT Markdown content
        """
        pass
