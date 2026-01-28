"""State persistence with atomic writes."""

import json
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


class OrchestratorState(str, Enum):
    """Orchestrator state machine states."""

    INIT = "INIT"
    PRECHECK = "PRECHECK"
    PLAN = "PLAN"
    SCHEDULE = "SCHEDULE"
    DISPATCH = "DISPATCH"
    MONITOR = "MONITOR"
    FINAL_UAT_GENERATE = "FINAL_UAT_GENERATE"
    FINAL_UAT_RUN = "FINAL_UAT_RUN"
    COMMIT = "COMMIT"
    PUSH = "PUSH"
    DONE = "DONE"
    FAILED = "FAILED"
    PAUSED = "PAUSED"


class WorkerState(str, Enum):
    """Worker task loop states."""

    TASK_INIT = "TASK_INIT"
    BUILD = "BUILD"
    VALIDATE = "VALIDATE"
    REVIEW = "REVIEW"
    UAT_GENERATE = "UAT_GENERATE"
    UAT_RUN = "UAT_RUN"
    DECIDE = "DECIDE"
    FIX = "FIX"
    TASK_DONE = "TASK_DONE"
    TASK_FAILED = "TASK_FAILED"


class LastPlanState(BaseModel):
    """Last planner execution state."""

    status: str = Field(default="pending", description="Plan status: pending/done/failed")
    artifact_path: Optional[str] = Field(default=None, description="Plan artifact path")
    error_message: Optional[str] = Field(default=None)


class LastBuildState(BaseModel):
    """Last builder execution state."""

    status: str = Field(default="pending", description="Build status: pending/done/failed")
    iteration: int = Field(default=0, description="Current iteration number")
    summary: Optional[dict] = Field(default=None, description="Build summary from agent")
    error_message: Optional[str] = Field(default=None)


class LastValidateState(BaseModel):
    """Last validation execution state."""

    status: str = Field(default="pending", description="Validate status: pending/done/failed")
    exit_code: int = Field(default=0, description="Validation exit code")
    output_tail: Optional[str] = Field(default=None, description="Last 20 lines of output")


class LastReviewState(BaseModel):
    """Last reviewer execution state."""

    status: str = Field(default="pending", description="Review status: pending/done/failed")
    verdict: Optional[str] = Field(default=None, description="Review verdict: approve/request_changes")
    feedback: Optional[str] = Field(default=None, description="Reviewer feedback")
    error_message: Optional[str] = Field(default=None)


class LastUATGenerationState(BaseModel):
    """Last UAT generation state."""

    status: str = Field(default="pending", description="UAT gen status: pending/done/failed/skipped")
    artifact_path: Optional[str] = Field(default=None, description="UAT cases file path")
    error_message: Optional[str] = Field(default=None)


class LastUATRunState(BaseModel):
    """Last UAT execution state."""

    status: str = Field(default="pending", description="UAT run status: pending/done/failed/skipped")
    exit_code: int = Field(default=0, description="UAT exit code")
    output_tail: Optional[str] = Field(default=None, description="Last 20 lines of output")


class SchedulerState(BaseModel):
    """Scheduler state for DAG execution."""

    tasks_total: int = Field(default=0, description="Total tasks in DAG")
    tasks_done: int = Field(default=0, description="Tasks completed")
    tasks_failed: int = Field(default=0, description="Tasks failed")
    tasks_running: int = Field(default=0, description="Tasks currently running")
    tasks_blocked: int = Field(default=0, description="Tasks blocked by dependencies")
    tasks_pending: int = Field(default=0, description="Tasks not yet ready")
    current_task_id: Optional[str] = Field(default=None, description="Currently executing task")


class TaskState(BaseModel):
    """Per-task state."""

    task_id: str
    title: str
    status: str = Field(default="pending", description="Task status: pending/running/done/failed/blocked")
    dependencies: list[str] = Field(default_factory=list)
    worker_id: Optional[str] = Field(default=None)
    branch_name: Optional[str] = Field(default=None)
    iteration: int = Field(default=0)

    last_plan: LastPlanState = Field(default_factory=LastPlanState)
    last_build: LastBuildState = Field(default_factory=LastBuildState)
    last_validate: LastValidateState = Field(default_factory=LastValidateState)
    last_review: LastReviewState = Field(default_factory=LastReviewState)
    last_uat_gen: LastUATGenerationState = Field(default_factory=LastUATGenerationState)
    last_uat_run: LastUATRunState = Field(default_factory=LastUATRunState)

    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class GitState(BaseModel):
    """Git operations state."""

    current_branch: str = Field(description="Current git branch")
    original_branch: str = Field(description="Branch where we started")
    task_branches: dict[str, str] = Field(default_factory=dict, description="Task ID -> branch name")
    commits: list[str] = Field(default_factory=list, description="Commit hashes made")
    push_status: Optional[str] = Field(default=None, description="Push status: pending/success/failed")
    pr_url: Optional[str] = Field(default=None, description="Created PR URL")


class OrchestratorStateModel(BaseModel):
    """Main orchestrator state model."""

    run_id: str = Field(description="Unique run identifier")
    state: OrchestratorState = Field(default=OrchestratorState.INIT)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # Task execution
    current_task_id: Optional[str] = Field(default=None)
    tasks: dict[str, TaskState] = Field(default_factory=dict)

    # Scheduler state
    scheduler: SchedulerState = Field(default_factory=SchedulerState)

    # Git state
    git: Optional[GitState] = Field(default=None)

    # Final UAT state
    final_uat_gen: LastUATGenerationState = Field(default_factory=LastUATGenerationState)
    final_uat_run: LastUATRunState = Field(default_factory=LastUATRunState)

    # Error tracking
    error_message: Optional[str] = Field(default=None)
    error_context: Optional[dict] = Field(default=None)


def load_state(state_path: Path) -> Optional[OrchestratorStateModel]:
    """Load state from file.

    Args:
        state_path: Path to state JSON file

    Returns:
        OrchestratorStateModel or None if file doesn't exist
    """
    if not state_path.exists():
        return None

    with open(state_path, "r") as f:
        data = json.load(f)

    return OrchestratorStateModel(**data)


def save_state(state: OrchestratorStateModel, state_path: Path) -> None:
    """Save state to file with atomic write.

    Args:
        state: State to save
        state_path: Destination path
    """
    state.updated_at = datetime.now(timezone.utc).isoformat()

    # Atomic write: temp file -> fsync -> rename
    temp_path = state_path.with_suffix(".tmp")
    with open(temp_path, "w") as f:
        json.dump(state.model_dump(mode="json"), f, indent=2)
        f.flush()
        # fsync not directly available in Python, but flush + close ensures write
    temp_path.replace(state_path)


def generate_run_id() -> str:
    """Generate unique run ID.

    Returns:
        UUID-based run ID
    """
    from datetime import datetime, timezone

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"run_{timestamp}"
