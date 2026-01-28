"""Unit tests for state machine."""

import pytest
from pathlib import Path
from src.state.machine import OrchestratorMachine, StateTransitionError
from src.state.persistence import OrchestratorState, OrchestratorStateModel


def test_orchestrator_machine_initialization(tmp_path):
    """Test OrchestratorMachine creates new state."""
    state_path = tmp_path / "state.json"
    machine = OrchestratorMachine(state_path)

    assert machine.current_state == OrchestratorState.INIT
    assert machine.state.run_id is not None
    assert state_path.exists()


def test_orchestrator_machine_resume(tmp_path):
    """Test OrchestratorMachine resumes existing state."""
    state_path = tmp_path / "state.json"

    # Create initial state
    machine1 = OrchestratorMachine(state_path)
    run_id = machine1.state.run_id

    # Resume with new machine instance
    machine2 = OrchestratorMachine(state_path)

    assert machine2.state.run_id == run_id
    assert machine2.current_state == OrchestratorState.INIT


def test_valid_transition(tmp_path):
    """Test valid state transition."""
    state_path = tmp_path / "state.json"
    machine = OrchestratorMachine(state_path)

    new_state = machine.transition(OrchestratorState.PRECHECK)

    assert new_state.state == OrchestratorState.PRECHECK
    assert machine.current_state == OrchestratorState.PRECHECK


def test_invalid_transition(tmp_path):
    """Test invalid state transition raises error."""
    state_path = tmp_path / "state.json"
    machine = OrchestratorMachine(state_path)

    # Can't go from INIT directly to DONE
    with pytest.raises(StateTransitionError):
        machine.transition(OrchestratorState.DONE)


def test_can_transition_to(tmp_path):
    """Test transition validation."""
    state_path = tmp_path / "state.json"
    machine = OrchestratorMachine(state_path)

    assert machine.can_transition_to(OrchestratorState.PRECHECK)
    assert not machine.can_transition_to(OrchestratorState.DONE)


def test_transition_with_error(tmp_path):
    """Test transition to FAILED state with error message."""
    state_path = tmp_path / "state.json"
    machine = OrchestratorMachine(state_path)

    new_state = machine.transition(
        OrchestratorState.FAILED,
        error_message="Test failure",
        error_context={"step": "PLAN"},
    )

    assert new_state.state == OrchestratorState.FAILED
    assert new_state.error_message == "Test failure"
    assert new_state.error_context == {"step": "PLAN"}


def test_update_task(tmp_path):
    """Test task state update."""
    state_path = tmp_path / "state.json"
    machine = OrchestratorMachine(state_path)

    machine.update_task(
        "task-1",
        title="Task 1",
        status="running",
    )

    task = machine.get_task("task-1")
    assert task is not None
    assert task.task_id == "task-1"
    assert task.title == "Task 1"
    assert task.status == "running"


def test_get_nonexistent_task(tmp_path):
    """Test getting non-existent task returns None."""
    state_path = tmp_path / "state.json"
    machine = OrchestratorMachine(state_path)

    task = machine.get_task("nonexistent")
    assert task is None
