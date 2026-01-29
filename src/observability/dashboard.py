"""Status dashboard and observability."""

import logging
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

from ..state.persistence import (
    OrchestratorState,
    OrchestratorStateModel,
    SchedulerState,
)

logger = logging.getLogger(__name__)


class StatusSymbol(str, Enum):
    """Symbols for status display."""

    DONE = "✅"
    RUNNING = "🔄"
    PENDING = "⏳"
    FAILED = "❌"
    BLOCKED = "🚫"


class StatusDashboard:
    """Generate and update status dashboard."""

    STATUS_FILE = Path(".autopilot/STATUS.md")
    ARTIFACTS_DIR = Path(".autopilot/artifacts")
    LOGS_DIR = Path(".autopilot/logs")

    def __init__(self, state: OrchestratorStateModel):
        """Initialize status dashboard.

        Args:
            state: Current orchestrator state
        """
        self.state = state

    def update(self) -> None:
        """Update STATUS.md file."""
        content = self._generate_status()
        self.STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)

        with open(self.STATUS_FILE, "w") as f:
            f.write(content)

        logger.debug(f"Updated {self.STATUS_FILE}")

    def _generate_status(self) -> str:
        """Generate status markdown content.

        Returns:
            Markdown content
        """
        lines = [
            "# Autopilot Run Status",
            "",
            f"**Run ID:** {self.state.run_id}",
            f"**Started:** {self.state.created_at}",
            f"**Updated:** {self.state.updated_at}",
            f"**Current State:** `{self.state.state.value}`",
            "",
            self._format_progress(),
            "",
            self._format_current_task(),
            "",
            self._format_scheduler_state(),
            "",
            self._format_git_state(),
            "",
            self._format_artifacts(),
        ]

        # Add error if present
        if self.state.error_message:
            lines.extend([
                "",
                "## ⚠️ Error",
                "",
                f"```\n{self.state.error_message}\n```",
            ])

        return "\n".join(lines)

    def _format_progress(self) -> str:
        """Format progress section.

        Returns:
            Markdown string
        """
        lines = [
            "## Progress",
            "",
        ]

        # Define state sequence
        state_order = [
            (OrchestratorState.INIT, "Initialize"),
            (OrchestratorState.PRECHECK, "Pre-check"),
            (OrchestratorState.PLAN, "Plan"),
            (OrchestratorState.SCHEDULE, "Schedule"),
            (OrchestratorState.DISPATCH, "Dispatch"),
            (OrchestratorState.MONITOR, "Monitor"),
            (OrchestratorState.FINAL_UAT_GENERATE, "Final UAT Generate"),
            (OrchestratorState.FINAL_UAT_RUN, "Final UAT Run"),
            (OrchestratorState.COMMIT, "Commit"),
            (OrchestratorState.PUSH, "Push"),
            (OrchestratorState.DONE, "Done"),
        ]

        # Find current state index
        try:
            current_idx = next(
                i
                for i, (state, _) in enumerate(state_order)
                if state == self.state.state
            )
        except StopIteration:
            # Error state
            current_idx = -1

        # Generate progress bars
        for i, (state, label) in enumerate(state_order):
            if state == OrchestratorState.DONE and self.state.state != OrchestratorState.DONE:
                continue  # Don't show done unless actually done

            if i < current_idx:
                symbol = StatusSymbol.DONE.value
            elif i == current_idx:
                symbol = StatusSymbol.RUNNING.value
            else:
                symbol = StatusSymbol.PENDING.value

            lines.append(f"- {symbol} {label}")

        return "\n".join(lines)

    def _format_current_task(self) -> str:
        """Format current task section.

        Returns:
            Markdown string
        """
        if not self.state.current_task_id:
            return "## Current Task\n\nNo task running"

        task = self.state.tasks.get(self.state.current_task_id)
        if not task:
            return f"## Current Task\n\nTask {self.state.current_task_id} not found"

        lines = [
            "## Current Task",
            "",
            f"**ID:** {task.task_id}",
            f"**Title:** {task.title}",
            f"**Status:** `{task.status}`",
            f"**Iteration:** {task.iteration}",
            "",
        ]

        # Add worker info if present
        if task.worker_id:
            lines.append(f"**Worker:** {task.worker_id}")
            lines.append("")

        return "\n".join(lines)

    def _format_scheduler_state(self) -> str:
        """Format scheduler state section.

        Returns:
            Markdown string
        """
        sched = self.state.scheduler

        lines = [
            "## Scheduler",
            "",
            f"**Total Tasks:** {sched.tasks_total}",
            f"**Done:** {sched.tasks_done}",
            f"**Running:** {sched.tasks_running}",
            f"**Failed:** {sched.tasks_failed}",
            f"**Blocked:** {sched.tasks_blocked}",
            f"**Pending:** {sched.tasks_pending}",
            "",
        ]

        # Add task list if there are tasks
        if self.state.tasks:
            lines.extend([
                "### Tasks",
                "",
            ])

            for task_id, task in self.state.tasks.items():
                status_symbol = self._get_task_symbol(task.status)
                lines.append(f"- {status_symbol} **{task_id}**: {task.title}")

            lines.append("")

        return "\n".join(lines)

    def _format_git_state(self) -> str:
        """Format git state section.

        Returns:
            Markdown string
        """
        if not self.state.git:
            return "## Git\n\nNo git operations yet"

        git = self.state.git

        lines = [
            "## Git",
            "",
            f"**Current Branch:** {git.current_branch}",
            f"**Original Branch:** {git.original_branch}",
            "",
        ]

        if git.task_branches:
            lines.extend([
                "### Task Branches",
                "",
            ])
            for task_id, branch in git.task_branches.items():
                lines.append(f"- `{task_id}` → `{branch}`")
            lines.append("")

        if git.commits:
            lines.extend([
                "### Commits",
                "",
                f"{len(git.commits)} commits made",
                "",
            ])

        if git.push_status:
            lines.extend([
                "### Push",
                "",
                f"**Status:** {git.push_status}",
                "",
            ])

        if git.pr_url:
            lines.extend([
                "### Pull Request",
                "",
                f"**URL:** {git.pr_url}",
                "",
            ])

        return "\n".join(lines)

    def _format_artifacts(self) -> str:
        """Format artifacts section.

        Returns:
            Markdown string
        """
        lines = [
            "## Artifacts",
            "",
        ]

        # Plan artifact
        plan_path = Path(".autopilot/plan/plan.json")
        if plan_path.exists():
            lines.append(f"- **Plan:** `.autopilot/plan/plan.json`")

        # DAG artifact
        dag_path = Path(".autopilot/plan/dag.json")
        if dag_path.exists():
            lines.append(f"- **DAG:** `.autopilot/plan/dag.json`")

        # State file
        lines.append(f"- **State:** `.autopilot/state.json`")

        # Logs directory
        if self.LOGS_DIR.exists():
            log_count = len(list(self.LOGS_DIR.glob("*.log")))
            lines.append(f"- **Logs:** `.autopilot/logs/` ({log_count} files)")

        # Artifacts directory
        if self.ARTIFACTS_DIR.exists():
            artifact_count = len(list(self.ARTIFACTS_DIR.glob("*")))
            lines.append(f"- **Artifacts:** `.autopilot/artifacts/` ({artifact_count} files)")

        lines.append("")
        return "\n".join(lines)

    def _get_task_symbol(self, status: str) -> str:
        """Get status symbol for task.

        Args:
            status: Task status string

        Returns:
            Status symbol emoji
        """
        status_lower = status.lower()

        if "done" in status_lower:
            return StatusSymbol.DONE.value
        elif "running" in status_lower:
            return StatusSymbol.RUNNING.value
        elif "failed" in status_lower:
            return StatusSymbol.FAILED.value
        elif "blocked" in status_lower:
            return StatusSymbol.BLOCKED.value
        else:
            return StatusSymbol.PENDING.value


class TerminalDashboard:
    """Live terminal dashboard."""

    def __init__(self, verbose: bool = False):
        """Initialize terminal dashboard.

        Args:
            verbose: Enable verbose output
        """
        self.verbose = verbose

    def print_state(self, state: OrchestratorStateModel) -> None:
        """Print current state to terminal.

        Args:
            state: Current orchestrator state
        """
        if not self.verbose:
            # Minimal output
            print(f"\r[{state.state.value}] {state.current_task_id or 'No task'}", end="", flush=True)
        else:
            # Verbose output
            print(f"\n=== Autopilot Status ===")
            print(f"Run ID: {state.run_id}")
            print(f"State: {state.state.value}")
            print(f"Task: {state.current_task_id or 'None'}")

            if state.scheduler:
                sched = state.scheduler
                print(f"\nScheduler: {sched.tasks_done}/{sched.tasks_total} done, {sched.tasks_running} running, {sched.tasks_failed} failed")

    def print_error(self, error: str) -> None:
        """Print error message.

        Args:
            error: Error message
        """
        print(f"\n❌ Error: {error}")

    def print_success(self, message: str) -> None:
        """Print success message.

        Args:
            message: Success message
        """
        print(f"\n✅ {message}")

    def print_info(self, message: str) -> None:
        """Print info message.

        Args:
            message: Info message
        """
        if self.verbose:
            print(f"ℹ️  {message}")

    def print_progress(self, message: str) -> None:
        """Print a progress message regardless of verbosity.

        Args:
            message: Progress message
        """
        print(f"\n⏳ {message}")
