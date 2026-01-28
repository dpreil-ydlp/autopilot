"""Multi-worker DAG scheduler."""

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

from ..tasks.plan import TaskDAG, compute_ready_set
from ..state.persistence import TaskState, WorkerState

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    """Task execution status."""

    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass
class SchedulerTask:
    """Scheduler task tracking."""

    task_id: str
    status: TaskStatus = TaskStatus.PENDING
    worker_id: Optional[str] = None
    retry_count: int = 0
    error_message: Optional[str] = None


@dataclass
class SchedulerStats:
    """Scheduler statistics."""

    total: int = 0
    pending: int = 0
    ready: int = 0
    running: int = 0
    done: int = 0
    failed: int = 0
    blocked: int = 0

    def __str__(self) -> str:
        """Return string representation."""
        return (
            f"Total: {self.total} | "
            f"Ready: {self.ready} | "
            f"Running: {self.running} | "
            f"Done: {self.done} | "
            f"Failed: {self.failed} | "
            f"Blocked: {self.blocked}"
        )


class WorkerPool:
    """Pool of workers for parallel task execution."""

    def __init__(self, max_workers: int, repo_root: Path, git_ops):
        """Initialize worker pool.

        Args:
            max_workers: Maximum number of parallel workers
            repo_root: Repository root path
            git_ops: GitOps instance for worktree management
        """
        self.max_workers = max_workers
        self.repo_root = repo_root
        self.git_ops = git_ops
        self.workers: dict[str, "Worker"] = {}

    async def dispatch(self, task_id: str) -> str:
        """Dispatch task to an available worker.

        Args:
            task_id: Task to dispatch

        Returns:
            Worker ID assigned to task
        """
        # Find or create available worker
        for worker_id, worker in self.workers.items():
            if not worker.is_busy():
                worker.assign(task_id)
                logger.info(f"Dispatched {task_id} to existing worker {worker_id}")
                return worker_id

        # Create new worker if under limit
        if len(self.workers) < self.max_workers:
            worker_id = f"worker-{len(self.workers) + 1}"
            worker = Worker(worker_id, self.repo_root, self.git_ops)
            worker.assign(task_id)
            self.workers[worker_id] = worker
            logger.info(f"Dispatched {task_id} to new worker {worker_id}")
            return worker_id

        # Wait for available worker
        logger.info(f"All workers busy, waiting for available worker...")
        while True:
            await asyncio.sleep(0.1)
            for worker_id, worker in self.workers.items():
                if not worker.is_busy():
                    worker.assign(task_id)
                    logger.info(f"Dispatched {task_id} to worker {worker_id}")
                    return worker_id

    def try_dispatch(self, task_id: str) -> Optional[str]:
        """Try to dispatch task without waiting.

        Args:
            task_id: Task to dispatch

        Returns:
            Worker ID if dispatched, None if no workers available
        """
        # Find or create available worker
        for worker_id, worker in self.workers.items():
            if not worker.is_busy():
                worker.assign(task_id)
                logger.info(f"Dispatched {task_id} to existing worker {worker_id}")
                return worker_id

        # Create new worker if under limit
        if len(self.workers) < self.max_workers:
            worker_id = f"worker-{len(self.workers) + 1}"
            worker = Worker(worker_id, self.repo_root, self.git_ops)
            worker.assign(task_id)
            self.workers[worker_id] = worker
            logger.info(f"Dispatched {task_id} to new worker {worker_id}")
            return worker_id

        # No workers available
        logger.debug(f"No workers available for {task_id}")
        return None

    def get_worker(self, worker_id: str) -> Optional["Worker"]:
        """Get worker by ID.

        Args:
            worker_id: Worker ID

        Returns:
            Worker or None
        """
        return self.workers.get(worker_id)

    def get_busy_workers(self) -> list[str]:
        """Get list of busy worker IDs.

        Returns:
            List of worker IDs
        """
        return [wid for wid, w in self.workers.items() if w.is_busy()]

    def get_available_workers(self) -> list[str]:
        """Get list of available worker IDs.

        Returns:
            List of worker IDs
        """
        return [wid for wid, w in self.workers.items() if not w.is_busy()]


class Worker:
    """Worker that executes tasks in isolated worktree."""

    def __init__(self, worker_id: str, repo_root: Path, git_ops):
        """Initialize worker.

        Args:
            worker_id: Worker identifier
            repo_root: Repository root path
            git_ops: GitOps instance for worktree management
        """
        self.worker_id = worker_id
        self.repo_root = repo_root
        self.git_ops = git_ops
        self.current_task: Optional[str] = None
        self.state = WorkerState.TASK_INIT
        self.worktree_path: Optional[Path] = None
        self.branch_name: Optional[str] = None

    def assign(self, task_id: str) -> None:
        """Assign task to worker.

        Args:
            task_id: Task to execute
        """
        self.current_task = task_id
        self.state = WorkerState.BUILD
        logger.debug(f"Worker {self.worker_id} assigned task {task_id}")

    def is_busy(self) -> bool:
        """Check if worker is busy.

        Returns:
            True if worker has active task
        """
        return self.current_task is not None

    def complete(self) -> None:
        """Mark current task as complete."""
        logger.info(f"Worker {self.worker_id} completed task {self.current_task}")
        self.current_task = None
        self.state = WorkerState.TASK_DONE

    def fail(self, error: str) -> None:
        """Mark current task as failed.

        Args:
            error: Error message
        """
        logger.error(f"Worker {self.worker_id} failed task {self.current_task}: {error}")
        self.current_task = None
        self.state = WorkerState.TASK_FAILED

    async def setup_worktree(self, branch_name: str) -> Path:
        """Set up isolated worktree for this worker.

        Args:
            branch_name: Branch name to checkout in worktree

        Returns:
            Path to worktree directory

        Raises:
            Exception: If worktree creation fails
        """
        self.branch_name = branch_name
        worktree_dir = self.repo_root / ".autopilot" / "worktrees"
        self.worktree_path = worktree_dir / self.worker_id

        # Create worktree if it doesn't exist
        if not await self.git_ops.worktree_exists(self.worktree_path):
            await self.git_ops.create_worktree(
                self.worktree_path,
                branch_name,
                force=False,
            )
        else:
            # Ensure we're on the right branch
            from ..utils.subprocess import SubprocessManager

            manager = SubprocessManager(timeout_sec=30)
            result = await manager.run(
                ["git", "checkout", "-B", branch_name],
                cwd=self.worktree_path,
            )
            if not result["success"]:
                raise Exception(f"Failed to checkout branch in worktree: {result['output']}")

        logger.info(f"Worker {self.worker_id} worktree ready: {self.worktree_path}")
        return self.worktree_path

    async def cleanup_worktree(self) -> None:
        """Clean up worker's worktree after task completion."""
        if self.worktree_path and self.worktree_path.exists():
            try:
                await self.git_ops.remove_worktree(self.worktree_path)
                logger.info(f"Worker {self.worker_id} cleaned up worktree")
            except Exception as e:
                logger.warning(f"Failed to cleanup worktree for {self.worker_id}: {e}")
        self.worktree_path = None
        self.branch_name = None

    async def execute_task(self, task, executor, scheduler) -> bool:
        """Execute assigned task using executor in isolated worktree.

        Args:
            task: Task to execute
            executor: ExecutionLoop instance with _execute_task method
            scheduler: DAGScheduler for state updates

        Returns:
            True if task succeeded
        """
        if not self.current_task:
            logger.error(f"Worker {self.worker_id} has no assigned task")
            return False

        # Create isolated worktree for this task
        branch_name = f"autopilot/{self.current_task}"
        worktree_path = None

        try:
            # Setup isolated environment
            worktree_path = await self.setup_worktree(branch_name)
            logger.info(f"Worker {self.worker_id} executing task {self.current_task} in {worktree_path}")

            # Execute task in worktree
            success = await executor._execute_task(task, scheduler, workdir=worktree_path)

            if success:
                self.complete()
            else:
                self.fail("Execution failed")

            return success

        except Exception as e:
            logger.error(f"Worker {self.worker_id} task execution failed: {e}")
            self.fail(str(e))
            return False

        finally:
            # Always cleanup worktree
            await self.cleanup_worktree()


class DAGScheduler:
    """Schedule and execute task DAG with multiple workers."""

    def __init__(
        self,
        dag: TaskDAG,
        max_workers: int = 1,
        work_dir: Optional[Path] = None,
        git_ops=None,
    ):
        """Initialize DAG scheduler.

        Args:
            dag: Task dependency graph
            max_workers: Maximum parallel workers
            work_dir: Working directory for execution
            git_ops: GitOps instance for worktree management
        """
        self.dag = dag
        self.max_workers = max_workers
        self.work_dir = work_dir or Path.cwd()
        self.git_ops = git_ops

        # Task tracking
        self.tasks: dict[str, SchedulerTask] = {
            task_id: SchedulerTask(task_id=task_id)
            for task_id in dag.tasks
        }

        # Completed tasks
        self.completed: set[str] = set()

        # Worker pool
        self.pool = WorkerPool(max_workers, work_dir, git_ops) if git_ops else WorkerPool(max_workers, work_dir, None)

    def get_stats(self) -> SchedulerStats:
        """Get scheduler statistics.

        Returns:
            SchedulerStats
        """
        stats = SchedulerStats(total=len(self.tasks))

        for task in self.tasks.values():
            stats.total += 0  # Already set
            if task.status == TaskStatus.PENDING:
                stats.pending += 1
            elif task.status == TaskStatus.READY:
                stats.ready += 1
            elif task.status == TaskStatus.RUNNING:
                stats.running += 1
            elif task.status == TaskStatus.DONE:
                stats.done += 1
            elif task.status == TaskStatus.FAILED:
                stats.failed += 1
            elif task.status == TaskStatus.BLOCKED:
                stats.blocked += 1

        return stats

    def update_task_states(self) -> None:
        """Update task states based on completed tasks."""
        # Get ready set
        ready = compute_ready_set(self.dag, self.completed)

        for task_id, task in self.tasks.items():
            if task_id in self.completed:
                task.status = TaskStatus.DONE
            elif task_id in ready:
                task.status = TaskStatus.READY
            elif task.status == TaskStatus.PENDING:
                # Check if blocked by dependencies
                deps = {dep for (dep, to) in self.dag.edges if to == task_id}
                if any(
                    self.tasks[dep].status == TaskStatus.FAILED
                    for dep in deps
                    if dep in self.tasks
                ):
                    task.status = TaskStatus.BLOCKED
                else:
                    task.status = TaskStatus.PENDING

    def get_ready_tasks(self) -> list[str]:
        """Get list of tasks ready to execute.

        Returns:
            List of task IDs
        """
        self.update_task_states()
        return [
            task_id
            for task_id, task in self.tasks.items()
            if task.status == TaskStatus.READY
        ]

    def mark_task_running(self, task_id: str, worker_id: str) -> None:
        """Mark task as running.

        Args:
            task_id: Task ID
            worker_id: Worker ID executing task
        """
        if task_id in self.tasks:
            self.tasks[task_id].status = TaskStatus.RUNNING
            self.tasks[task_id].worker_id = worker_id
            logger.info(f"Task {task_id} marked as running on {worker_id}")

    def mark_task_complete(self, task_id: str) -> None:
        """Mark task as complete.

        Args:
            task_id: Task ID
        """
        if task_id in self.tasks:
            self.tasks[task_id].status = TaskStatus.DONE
            self.completed.add(task_id)
            logger.info(f"Task {task_id} marked as complete")

    def mark_task_failed(self, task_id: str, error: str) -> None:
        """Mark task as failed.

        Args:
            task_id: Task ID
            error: Error message
        """
        if task_id in self.tasks:
            self.tasks[task_id].status = TaskStatus.FAILED
            self.tasks[task_id].error_message = error
            logger.error(f"Task {task_id} marked as failed: {error}")

    def is_complete(self) -> bool:
        """Check if all tasks are complete.

        Returns:
            True if all tasks are DONE or FAILED
        """
        return all(
            task.status in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.BLOCKED)
            for task in self.tasks.values()
        )

    def has_failures(self) -> bool:
        """Check if any tasks failed.

        Returns:
            True if any tasks are FAILED
        """
        return any(
            task.status == TaskStatus.FAILED
            for task in self.tasks.values()
        )

    def get_failed_tasks(self) -> list[str]:
        """Get list of failed task IDs.

        Returns:
            List of task IDs
        """
        return [
            task_id
            for task_id, task in self.tasks.items()
            if task.status == TaskStatus.FAILED
        ]
