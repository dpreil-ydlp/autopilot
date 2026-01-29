"""Orchestrator state machine implementation."""

import logging
from pathlib import Path

from .persistence import (
    OrchestratorState,
    OrchestratorStateModel,
    TaskState,
    save_state,
)

logger = logging.getLogger(__name__)


class StateTransitionError(Exception):
    """Invalid state transition."""

    pass


class OrchestratorMachine:
    """Orchestrator state machine."""

    # Valid state transitions
    TRANSITIONS = {
        OrchestratorState.INIT: [
            OrchestratorState.PRECHECK,
            OrchestratorState.FAILED,
        ],
        OrchestratorState.PRECHECK: [
            OrchestratorState.PLAN,
            OrchestratorState.FAILED,
        ],
        OrchestratorState.PLAN: [
            OrchestratorState.SCHEDULE,
            OrchestratorState.FAILED,
        ],
        OrchestratorState.SCHEDULE: [
            OrchestratorState.DISPATCH,
            OrchestratorState.FAILED,
        ],
        OrchestratorState.DISPATCH: [
            OrchestratorState.MONITOR,
        ],
        OrchestratorState.MONITOR: [
            OrchestratorState.MONITOR,  # Continue monitoring
            OrchestratorState.FINAL_UAT_GENERATE,
            OrchestratorState.FAILED,
            OrchestratorState.PAUSED,
        ],
        OrchestratorState.FINAL_UAT_GENERATE: [
            OrchestratorState.FINAL_UAT_RUN,
            OrchestratorState.FAILED,
        ],
        OrchestratorState.FINAL_UAT_RUN: [
            OrchestratorState.COMMIT,
            OrchestratorState.FAILED,
        ],
        OrchestratorState.COMMIT: [
            OrchestratorState.PUSH,
            OrchestratorState.FAILED,
        ],
        OrchestratorState.PUSH: [
            OrchestratorState.DONE,
            OrchestratorState.FAILED,
        ],
        OrchestratorState.FAILED: [],  # Terminal
        OrchestratorState.DONE: [],  # Terminal
        OrchestratorState.PAUSED: [
            OrchestratorState.MONITOR,  # Resume
            OrchestratorState.FAILED,
        ],
    }

    def __init__(self, state_path: Path):
        """Initialize state machine.

        Args:
            state_path: Path to state file
        """
        self.state_path = state_path
        self.state = self._load_or_create()

    def _load_or_create(self) -> OrchestratorStateModel:
        """Load existing state or create new one."""
        existing = None
        if self.state_path.exists():
            from .persistence import load_state

            existing = load_state(self.state_path)
            if existing:
                logger.info(f"Resuming run {existing.run_id} in state {existing.state}")
                return existing

        # Create new state
        from .persistence import generate_run_id

        new_state = OrchestratorStateModel(
            run_id=generate_run_id(),
            state=OrchestratorState.INIT,
        )
        save_state(new_state, self.state_path)
        return new_state

    @property
    def current_state(self) -> OrchestratorState:
        """Get current state."""
        return self.state.state

    def can_transition_to(self, new_state: OrchestratorState) -> bool:
        """Check if transition is valid.

        Args:
            new_state: Target state

        Returns:
            True if transition is valid
        """
        valid_targets = self.TRANSITIONS.get(self.current_state, [])
        return new_state in valid_targets

    def transition(
        self,
        new_state: OrchestratorState,
        error_message: str | None = None,
        error_context: dict | None = None,
    ) -> OrchestratorStateModel:
        """Execute state transition.

        Args:
            new_state: Target state
            error_message: Optional error message (for FAILED state)
            error_context: Optional error context dict

        Returns:
            Updated state

        Raises:
            StateTransitionError: If transition is invalid
        """
        if not self.can_transition_to(new_state):
            raise StateTransitionError(
                f"Invalid transition from {self.current_state} to {new_state}"
            )

        logger.info(f"State transition: {self.current_state} -> {new_state}")

        self.state.state = new_state

        if error_message:
            self.state.error_message = error_message
        if error_context:
            self.state.error_context = error_context

        save_state(self.state, self.state_path)
        return self.state

    def update_task(self, task_id: str, **updates) -> None:
        """Update task state.

        Args:
            task_id: Task identifier
            **updates: Fields to update
        """
        if task_id not in self.state.tasks:
            self.state.tasks[task_id] = TaskState(
                task_id=task_id, title=updates.get("title", task_id)
            )

        task = self.state.tasks[task_id]
        for key, value in updates.items():
            setattr(task, key, value)

        task.updated_at = self.state.updated_at
        save_state(self.state, self.state_path)

    def get_task(self, task_id: str) -> TaskState | None:
        """Get task state.

        Args:
            task_id: Task identifier

        Returns:
            TaskState or None
        """
        return self.state.tasks.get(task_id)
