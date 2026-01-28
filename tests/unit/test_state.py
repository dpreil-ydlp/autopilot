"""Unit tests for state management."""

import pytest
from datetime import datetime
from src.state.persistence import (
    OrchestratorState,
    WorkerState,
    TaskState,
    OrchestratorStateModel,
    generate_run_id,
)


def test_orchestrator_state_enum():
    """Test OrchestratorState enum values."""
    assert OrchestratorState.INIT == "INIT"
    assert OrchestratorState.DONE == "DONE"
    assert OrchestratorState.FAILED == "FAILED"
    assert OrchestratorState.PAUSED == "PAUSED"


def test_worker_state_enum():
    """Test WorkerState enum values."""
    assert WorkerState.TASK_INIT == "TASK_INIT"
    assert WorkerState.BUILD == "BUILD"
    assert WorkerState.TASK_DONE == "TASK_DONE"
    assert WorkerState.TASK_FAILED == "TASK_FAILED"


def test_task_state_creation():
    """Test TaskState model creation."""
    task = TaskState(
        task_id="task-1",
        title="Test Task",
    )
    assert task.task_id == "task-1"
    assert task.title == "Test Task"
    assert task.status == "pending"
    assert task.iteration == 0
    assert len(task.dependencies) == 0


def test_task_state_with_dependencies():
    """Test TaskState with dependencies."""
    task = TaskState(
        task_id="task-2",
        title="Dependent Task",
        dependencies=["task-1"],
    )
    assert len(task.dependencies) == 1
    assert task.dependencies[0] == "task-1"


def test_orchestrator_state_creation():
    """Test OrchestratorStateModel creation."""
    state = OrchestratorStateModel(
        run_id="test-run-1",
        state=OrchestratorState.INIT,
    )
    assert state.run_id == "test-run-1"
    assert state.state == OrchestratorState.INIT
    assert state.created_at is not None
    assert state.updated_at is not None


def test_orchestrator_state_with_tasks():
    """Test OrchestratorStateModel with tasks."""
    state = OrchestratorStateModel(
        run_id="test-run-2",
        state=OrchestratorState.MONITOR,
        current_task_id="task-1",
        tasks={
            "task-1": TaskState(task_id="task-1", title="Task 1"),
        },
    )
    assert state.current_task_id == "task-1"
    assert len(state.tasks) == 1
    assert "task-1" in state.tasks


def test_generate_run_id():
    """Test run ID generation."""
    run_id = generate_run_id()
    assert run_id.startswith("run_")
    assert len(run_id) > 10  # Should have timestamp


def test_task_state_timestamps():
    """Test TaskState timestamps are ISO format."""
    task = TaskState(
        task_id="task-1",
        title="Test Task",
    )
    # Should be ISO 8601 format
    datetime.fromisoformat(task.created_at)
    datetime.fromisoformat(task.updated_at)
