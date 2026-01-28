"""Worker task loop state machine implementation."""

import logging
from typing import Optional

from .persistence import (
    TaskState,
    WorkerState,
    LastBuildState,
    LastValidateState,
    LastReviewState,
    LastUATGenerationState,
    LastUATRunState,
)

logger = logging.getLogger(__name__)


class WorkerLoop:
    """Worker task loop state machine."""

    def __init__(self, task: TaskState):
        """Initialize worker loop for task.

        Args:
            task: Task state to manage
        """
        self.task = task

    @property
    def current_state(self) -> WorkerState:
        """Determine current worker state from task state."""
        # Check states in priority order
        if self.task.last_uat_run.status == "running":
            return WorkerState.UAT_RUN
        if self.task.last_uat_gen.status == "running":
            return WorkerState.UAT_GENERATE
        if self.task.last_review.status == "running":
            return WorkerState.REVIEW
        if self.task.last_validate.status == "running":
            return WorkerState.VALIDATE
        if self.task.last_build.status == "running":
            return WorkerState.BUILD

        # If nothing running, check what's pending
        if self.task.last_build.status in ("pending", None):
            return WorkerState.TASK_INIT
        if self.task.last_build.status == "done" and self.task.last_validate.status in ("pending", None):
            return WorkerState.BUILD
        if self.task.last_validate.status == "done" and self.task.last_review.status in ("pending", None):
            return WorkerState.VALIDATE
        if self.task.last_review.status == "done" and self.task.last_uat_gen.status in ("pending", None):
            return WorkerState.REVIEW
        if self.task.last_uat_gen.status == "done" and self.task.last_uat_run.status in ("pending", None):
            return WorkerState.UAT_GENERATE

        # Check if in FIX loop
        if self.task.last_build.status == "failed" and self.task.iteration > 0:
            return WorkerState.DECIDE

        # Terminal states
        if self.task.last_uat_run.status == "done":
            return WorkerState.TASK_DONE
        if any(
            state.status == "failed"
            for state in [
                self.task.last_build,
                self.task.last_validate,
                self.task.last_review,
                self.task.last_uat_gen,
                self.task.last_uat_run,
            ]
        ):
            return WorkerState.TASK_FAILED

        return WorkerState.TASK_INIT

    def should_continue_loop(self) -> bool:
        """Check if worker should continue looping.

        Returns:
            True if max iterations not exceeded
        """
        from ..config.models import AutopilotConfig

        # This would need config passed in - simplified for now
        max_iterations = 10
        return self.task.iteration < max_iterations

    def decide_next_action(self) -> WorkerState:
        """Decide next action after review.

        Returns:
            Next worker state (FIX or UAT_GENERATE or TASK_DONE)
        """
        if self.task.last_review.verdict == "approve":
            # Review passed, move to UAT
            if self.task.last_uat_gen.status in ("pending", None):
                return WorkerState.UAT_GENERATE
            elif self.task.last_uat_run.status in ("pending", None):
                return WorkerState.UAT_RUN
            else:
                return WorkerState.TASK_DONE
        else:
            # Review failed, enter FIX loop if we have iterations left
            if self.should_continue_loop():
                return WorkerState.FIX
            else:
                return WorkerState.TASK_FAILED

    def start_build(self) -> None:
        """Mark build as started."""
        self.task.last_build = LastBuildState(status="running", iteration=self.task.iteration)

    def complete_build(self, summary: dict) -> None:
        """Mark build as complete.

        Args:
            summary: Build summary from agent
        """
        self.task.last_build.status = "done"
        self.task.last_build.summary = summary

    def fail_build(self, error_message: str) -> None:
        """Mark build as failed.

        Args:
            error_message: Error message
        """
        self.task.last_build.status = "failed"
        self.task.last_build.error_message = error_message

    def start_validate(self) -> None:
        """Mark validation as started."""
        self.task.last_validate = LastValidateState(status="running")

    def complete_validate(self, exit_code: int, output_tail: str) -> None:
        """Mark validation as complete.

        Args:
            exit_code: Validation exit code
            output_tail: Last 20 lines of output
        """
        self.task.last_validate.status = "done" if exit_code == 0 else "failed"
        self.task.last_validate.exit_code = exit_code
        self.task.last_validate.output_tail = output_tail

    def start_review(self) -> None:
        """Mark review as started."""
        self.task.last_review = LastReviewState(status="running")

    def complete_review(self, verdict: str, feedback: str) -> None:
        """Mark review as complete.

        Args:
            verdict: Review verdict (approve/request_changes)
            feedback: Reviewer feedback
        """
        self.task.last_review.status = "done"
        self.task.last_review.verdict = verdict
        self.task.last_review.feedback = feedback

    def fail_review(self, error_message: str) -> None:
        """Mark review as failed.

        Args:
            error_message: Error message
        """
        self.task.last_review.status = "failed"
        self.task.last_review.error_message = error_message

    def start_uat_generation(self) -> None:
        """Mark UAT generation as started."""
        self.task.last_uat_gen = LastUATGenerationState(status="running")

    def complete_uat_generation(self, artifact_path: str) -> None:
        """Mark UAT generation as complete.

        Args:
            artifact_path: Path to UAT cases file
        """
        self.task.last_uat_gen.status = "done"
        self.task.last_uat_gen.artifact_path = artifact_path

    def skip_uat_generation(self) -> None:
        """Mark UAT generation as skipped (no command configured)."""
        self.task.last_uat_gen.status = "skipped"

    def start_uat_run(self) -> None:
        """Mark UAT run as started."""
        self.task.last_uat_run = LastUATRunState(status="running")

    def complete_uat_run(self, exit_code: int, output_tail: str) -> None:
        """Mark UAT run as complete.

        Args:
            exit_code: UAT exit code
            output_tail: Last 20 lines of output
        """
        self.task.last_uat_run.status = "done" if exit_code == 0 else "failed"
        self.task.last_uat_run.exit_code = exit_code
        self.task.last_uat_run.output_tail = output_tail

    def skip_uat_run(self) -> None:
        """Mark UAT run as skipped (no command configured)."""
        self.task.last_uat_run.status = "skipped"

    def increment_iteration(self) -> None:
        """Increment iteration counter for FIX loop."""
        self.task.iteration += 1
