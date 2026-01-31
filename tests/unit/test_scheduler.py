"""Unit tests for scheduler."""

from pathlib import Path

from src.scheduler.dag import (
    DAGScheduler,
    SchedulerStats,
    SchedulerTask,
    TaskStatus,
    Worker,
    WorkerPool,
)
from src.tasks.plan import TaskDAG


def test_scheduler_task():
    """Test SchedulerTask creation."""
    task = SchedulerTask(task_id="task-1")

    assert task.task_id == "task-1"
    assert task.status == TaskStatus.PENDING
    assert task.worker_id is None
    assert task.retry_count == 0


def test_worker():
    """Test Worker operations."""
    from unittest.mock import Mock

    repo_root = Path("/tmp/test-repo")
    git_ops = Mock()

    worker = Worker("worker-1", repo_root, git_ops)

    assert worker.worker_id == "worker-1"
    assert not worker.is_busy()
    assert worker.current_task is None

    # Assign task
    worker.assign("task-1")
    assert worker.is_busy()
    assert worker.current_task == "task-1"

    # Complete task
    worker.complete()
    assert not worker.is_busy()
    assert worker.current_task is None


def test_worker_pool():
    """Test WorkerPool operations."""
    from unittest.mock import Mock

    repo_root = Path("/tmp/test-repo")
    git_ops = Mock()

    pool = WorkerPool(max_workers=2, repo_root=repo_root, git_ops=git_ops)

    assert pool.max_workers == 2
    assert len(pool.workers) == 0


def test_dag_scheduler_initialization():
    """Test DAGScheduler initialization."""
    dag = TaskDAG(
        tasks={"task1": None, "task2": None},
        edges=[],
        topo_order=["task1", "task2"],
        parallel_batches=[],
    )

    scheduler = DAGScheduler(dag, max_workers=2, git_ops=None)

    assert scheduler.max_workers == 2
    assert len(scheduler.tasks) == 2
    assert "task1" in scheduler.tasks
    assert "task2" in scheduler.tasks
    assert scheduler.dag == dag


def test_scheduler_stats():
    """Test SchedulerStats."""
    stats = SchedulerStats(
        total=10,
        pending=2,
        ready=3,
        running=1,
        done=3,
        failed=1,
        blocked=0,
    )

    assert "Total: 10" in str(stats)
    assert "Ready: 3" in str(stats)
    assert "Done: 3" in str(stats)


def test_mark_task_transitions():
    """Test marking task through lifecycle."""
    dag = TaskDAG(
        tasks={"task1": None},
        edges=[],
        topo_order=["task1"],
        parallel_batches=[],
    )

    scheduler = DAGScheduler(dag, git_ops=None)

    # Initially pending
    assert scheduler.tasks["task1"].status == TaskStatus.PENDING

    # Mark running
    scheduler.mark_task_running("task1", "worker-1")
    assert scheduler.tasks["task1"].status == TaskStatus.RUNNING
    assert scheduler.tasks["task1"].worker_id == "worker-1"

    # Mark complete
    scheduler.mark_task_complete("task1")
    assert scheduler.tasks["task1"].status == TaskStatus.DONE
    assert "task1" in scheduler.completed


def test_mark_task_failed():
    """Test marking task as failed."""
    dag = TaskDAG(
        tasks={"task1": None},
        edges=[],
        topo_order=["task1"],
        parallel_batches=[],
    )

    scheduler = DAGScheduler(dag, git_ops=None)
    scheduler.mark_task_failed("task1", "Test error")

    assert scheduler.tasks["task1"].status == TaskStatus.FAILED
    assert scheduler.tasks["task1"].error_message == "Test error"
    assert scheduler.has_failures()


def test_is_complete():
    """Test completion detection."""
    dag = TaskDAG(
        tasks={"task1": None, "task2": None},
        edges=[],
        topo_order=["task1", "task2"],
        parallel_batches=[],
    )

    scheduler = DAGScheduler(dag, git_ops=None)

    # Not complete initially
    assert not scheduler.is_complete()

    # Still not complete with one done
    scheduler.mark_task_complete("task1")
    assert not scheduler.is_complete()

    # Complete when all done
    scheduler.mark_task_complete("task2")
    assert scheduler.is_complete()

    # Also complete if one failed
    scheduler2 = DAGScheduler(dag, git_ops=None)
    scheduler2.mark_task_complete("task1")
    scheduler2.mark_task_failed("task2", "error")
    assert scheduler2.is_complete()


def test_failed_tasks_are_not_rescheduled():
    """A failed task should not flip back to READY on subsequent scheduler ticks."""
    dag = TaskDAG(
        tasks={"task1": None},
        edges=[],
        topo_order=["task1"],
        parallel_batches=[],
    )

    scheduler = DAGScheduler(dag, git_ops=None)
    assert scheduler.get_ready_tasks() == ["task1"]

    scheduler.mark_task_failed("task1", "boom")
    assert scheduler.tasks["task1"].status == TaskStatus.FAILED

    # Should remain FAILED and not be considered ready again.
    assert scheduler.get_ready_tasks() == []
    assert scheduler.tasks["task1"].status == TaskStatus.FAILED


def test_failed_dependency_blocks_downstream():
    """Downstream tasks should become BLOCKED when an upstream dep fails."""
    dag = TaskDAG(
        tasks={"task1": None, "task2": None},
        edges=[("task1", "task2")],
        topo_order=["task1", "task2"],
        parallel_batches=[],
    )

    scheduler = DAGScheduler(dag, git_ops=None)
    assert scheduler.get_ready_tasks() == ["task1"]

    scheduler.mark_task_failed("task1", "boom")
    scheduler.update_task_states()

    assert scheduler.tasks["task1"].status == TaskStatus.FAILED
    assert scheduler.tasks["task2"].status == TaskStatus.BLOCKED
    assert scheduler.get_ready_tasks() == []
