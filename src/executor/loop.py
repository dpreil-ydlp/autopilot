"""Main execution loop orchestrating all components."""

import asyncio
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path

from ..agents.base import AgentError
from ..agents.claude import ClaudeAgent
from ..agents.codex import CodexAgent
from ..config.models import AutopilotConfig
from ..integrations.github import GitHubIntegration
from ..observability.dashboard import StatusDashboard, TerminalDashboard
from ..safety.guards import SafetyChecker
from ..scheduler.dag import DAGScheduler
from ..state.machine import OrchestratorMachine, StateTransitionError
from ..state.persistence import (
    OrchestratorState,
)
from ..tasks.parser import parse_task_file, validate_task_constraints
from ..tasks.plan import TaskDAG, expand_plan
from ..utils.git import GitOps
from ..utils.subprocess import SubprocessManager
from ..validation.runner import ValidationRunner

logger = logging.getLogger(__name__)


class ExecutionLoop:
    """Main execution loop for Autopilot."""

    _AUTO_RESOLVE_CONFLICT_PREFIXES = ("logs/", ".autopilot/logs/", ".autopilot/artifacts/")

    def __init__(
        self,
        config: AutopilotConfig,
        state_path: Path | None = None,
        resume: bool = False,
        verbose: bool = False,
    ):
        """Initialize execution loop.

        Args:
            config: Autopilot configuration
            state_path: Path to state file
            resume: Whether to resume an existing run from state.json
            verbose: Enable verbose output
        """
        self.config = config
        self.verbose = verbose
        self.max_workers = 4  # Default, can be overridden per execution
        self._merge_lock = asyncio.Lock()

        # State management
        state_path = state_path or Path(".autopilot/state.json")
        self.machine = OrchestratorMachine(state_path, resume=resume)
        self.terminal = TerminalDashboard(verbose=verbose)
        self.dashboard = StatusDashboard(self.machine.state)

        # Initialize components
        self.git_ops = GitOps(
            repo_root=config.repo.root,
            timeout_sec=30,
        )
        self.safety = SafetyChecker(
            git_ops=self.git_ops,
            allowed_paths=config.safety.allowed_paths,
            denied_paths=config.safety.denied_paths,
            diff_lines_cap=config.safety.diff_lines_cap,
            max_todo_count=config.safety.max_todo_count,
        )

        # Agents
        builder_cfg = config.builder.model_dump()
        builder_mode = (builder_cfg.get("mode") or "claude").lower()
        if builder_mode == "claude":
            self.builder = ClaudeAgent(builder_cfg)
        else:
            self.builder = CodexAgent(builder_cfg)

        reviewer_cfg = config.reviewer.model_dump()
        reviewer_mode = (reviewer_cfg.get("mode") or "codex_cli").lower()
        if reviewer_mode == "claude":
            self.reviewer = ClaudeAgent(reviewer_cfg)
        else:
            self.reviewer = CodexAgent(reviewer_cfg)

        planner_cfg = config.planner.model_dump()
        planner_mode = (planner_cfg.get("mode") or "codex_cli").lower()
        if planner_mode == "claude":
            self.planner = ClaudeAgent(planner_cfg)
        else:
            self.planner = CodexAgent(planner_cfg)

        # GitHub integration (if enabled)
        self.github = None
        if config.github.enabled:
            self.github = GitHubIntegration(
                git_ops=self.git_ops,
                remote=config.github.remote_name,
                base_branch=config.github.base_branch,
            )

    def _transition_state(
        self,
        new_state: OrchestratorState,
        *,
        error_message: str | None = None,
        error_context: dict | None = None,
    ) -> None:
        """Best-effort orchestrator state transition for truthful status reporting."""
        if self.machine.current_state == new_state:
            return
        if not self.machine.can_transition_to(new_state):
            logger.warning(
                "Skipping invalid orchestrator transition: %s -> %s",
                self.machine.current_state,
                new_state,
            )
            return
        try:
            self.machine.transition(
                new_state,
                error_message=error_message,
                error_context=error_context,
            )
        except StateTransitionError as e:
            logger.warning("State transition failed (%s); continuing", e)
            return
        try:
            self.dashboard.update()
        except Exception:
            pass

    async def run_single_task(self, task_path: Path) -> bool:
        """Run a single task file.

        Args:
            task_path: Path to task file

        Returns:
            True if task succeeded
        """
        logger.info(f"Running single task: {task_path}")

        try:
            if self.machine.current_state == OrchestratorState.INIT:
                self._transition_state(OrchestratorState.PRECHECK)

            # Parse task
            task = parse_task_file(task_path)
            violations = validate_task_constraints(task)

            if violations:
                logger.error(f"Task validation failed: {violations}")
                self._transition_state(
                    OrchestratorState.FAILED,
                    error_message=f"Task validation failed: {violations}",
                    error_context={"task": str(task_path)},
                )
                return False

            # Check safety
            await self.safety.check_before_transition()

            self._transition_state(OrchestratorState.PLAN)

            # Create DAG with single task
            dag = TaskDAG(
                tasks={task.task_id: task},
                edges=[],
                topo_order=[task.task_id],
                parallel_batches=[[task.task_id]],
            )

            self._transition_state(OrchestratorState.SCHEDULE)

            # Execute task
            return await self._execute_dag(dag, max_workers=1)  # Single task = 1 worker

        except Exception as e:
            logger.error(f"Task execution failed: {e}")
            self._transition_state(
                OrchestratorState.FAILED,
                error_message=str(e),
                error_context={"task": str(task_path)},
            )
            return False

    async def run_plan(self, plan_path: Path, max_workers: int = 4) -> bool:
        """Run a plan file.

        Args:
            plan_path: Path to plan file
            max_workers: Maximum parallel workers

        Returns:
            True if plan succeeded
        """
        logger.info(f"Running plan: {plan_path}")

        try:
            if self.machine.current_state == OrchestratorState.INIT:
                self._transition_state(OrchestratorState.PRECHECK)

            self.terminal.print_progress("Submitting plan to Codex for DAG expansion")
            self._transition_state(OrchestratorState.PLAN)

            # Expand plan into DAG
            dag = await expand_plan(
                plan_path=plan_path,
                planner_config=self.config.planner.model_dump(),
                progress_callback=self.terminal.print_progress,
            )
            self.terminal.print_progress(
                f"DAG ready: {len(dag.tasks)} tasks, {len(dag.edges)} edges, {len(dag.parallel_batches)} batches"
            )

            # Validate DAG
            from ..tasks.plan import validate_dag

            errors = validate_dag(dag)

            if errors:
                logger.error(f"DAG validation failed: {errors}")
                self._transition_state(
                    OrchestratorState.FAILED,
                    error_message=f"DAG validation failed: {errors}",
                    error_context={"plan": str(plan_path)},
                )
                return False

            self._transition_state(OrchestratorState.SCHEDULE)

            # Execute DAG
            return await self._execute_dag(dag, max_workers=max_workers)

        except Exception as e:
            logger.error(f"Plan execution failed: {e}")
            self._transition_state(
                OrchestratorState.FAILED,
                error_message=str(e),
                error_context={"plan": str(plan_path)},
            )
            return False

    async def _execute_dag(self, dag: TaskDAG, max_workers: int | None = None) -> bool:
        """Execute task DAG.

        Args:
            dag: Task dependency graph
            max_workers: Maximum parallel workers (overrides default)

        Returns:
            True if all tasks succeeded
        """
        # Use provided max_workers or default
        workers = max_workers or self.max_workers
        self._transition_state(OrchestratorState.DISPATCH)
        self._transition_state(OrchestratorState.MONITOR)

        # Create scheduler with git_ops for worktree support
        scheduler = DAGScheduler(
            dag=dag,
            max_workers=workers,
            work_dir=self.config.repo.root,
            git_ops=self.git_ops,
        )

        if not dag.tasks:
            logger.error("No tasks to execute in DAG")
            return False

        # Initialize persisted scheduler stats for UX/monitoring.
        stats = scheduler.get_stats()
        self.machine.update_scheduler(
            tasks_total=stats.total,
            tasks_done=stats.done,
            tasks_failed=stats.failed,
            tasks_running=stats.running,
            tasks_blocked=stats.blocked,
            tasks_pending=stats.pending + stats.ready,
            current_task_id=None,
        )

        # Update state
        self.machine.update_task(
            list(dag.tasks.keys())[0],
            title=list(dag.tasks.values())[0].title,
        )

        # Execute until complete
        while not scheduler.is_complete():
            # Check safety
            await self.safety.check_before_transition()

            # Get ready tasks
            ready_tasks = scheduler.get_ready_tasks()

            if not ready_tasks:
                if scheduler.has_failures():
                    logger.error("Tasks failed, blocking remaining tasks")
                    self._transition_state(
                        OrchestratorState.FAILED,
                        error_message="Tasks failed, blocking remaining tasks",
                        error_context={"run_id": self.machine.state.run_id},
                    )
                    return False
                elif not scheduler.is_complete():
                    logger.warning("No ready tasks but not complete - waiting")
                    await asyncio.sleep(1)
                    continue
                else:
                    break

            # Dispatch ready tasks to available workers (non-blocking)
            # Only dispatch as many tasks as we have workers for
            dispatched = []
            for task_id in ready_tasks:
                worker_id = scheduler.pool.try_dispatch(task_id)
                if worker_id:
                    worker = scheduler.pool.get_worker(worker_id)
                    dispatched.append((task_id, worker_id, worker))

                # Stop if we've dispatched max_workers tasks
                if len(dispatched) >= workers:
                    break

            if not dispatched:
                # All workers busy, wait a bit then retry
                logger.info("All workers busy, waiting for task completion...")
                await asyncio.sleep(0.5)
                continue

            logger.info(f"Executing {len(dispatched)} tasks on {workers} workers")

            # Update persisted state as soon as tasks are dispatched, so `autopilot status` and
            # STATUS.md reflect reality while tasks are running (not only after they finish).
            for task_id, worker_id, _ in dispatched:
                scheduler.mark_task_running(task_id, worker_id=worker_id)
                try:
                    task = dag.tasks[task_id]
                    self.machine.update_task(
                        task_id,
                        title=task.title,
                        status="running",
                        worker_id=worker_id,
                    )
                except Exception:
                    # Best-effort only: never block execution on state reporting failures.
                    pass
            stats = scheduler.get_stats()
            self.machine.update_scheduler(
                tasks_total=stats.total,
                tasks_done=stats.done,
                tasks_failed=stats.failed,
                tasks_running=stats.running,
                tasks_blocked=stats.blocked,
                tasks_pending=stats.pending + stats.ready,
                current_task_id=self.machine.state.current_task_id,
            )
            self.dashboard.update()
            self.terminal.print_state(self.machine.state)

            # Execute all dispatched tasks concurrently
            async def execute_on_worker(task_id: str, worker_id: str, worker):
                """Execute task on assigned worker."""
                try:
                    task = dag.tasks[task_id]
                    success = await worker.execute_task(task, self, scheduler)
                    return task_id, worker_id, success
                except Exception as e:
                    logger.error(f"Task {task_id} raised exception: {e}")
                    return task_id, worker_id, False

            # Run all dispatched tasks concurrently
            results = await asyncio.gather(
                *[execute_on_worker(tid, wid, w) for tid, wid, w in dispatched],
                return_exceptions=True,
            )

            # Process results
            for result in results:
                if isinstance(result, Exception):
                    logger.error(f"Worker execution raised exception: {result}")
                    continue

                task_id, worker_id, success = result
                if success:
                    scheduler.mark_task_complete(task_id)
                    logger.info(f"Task completed: {task_id} (worker: {worker_id})")
                else:
                    scheduler.mark_task_failed(task_id, "Execution failed")
                    logger.error(f"Task failed: {task_id} (worker: {worker_id})")

            # Update dashboard
            stats = scheduler.get_stats()
            self.machine.update_scheduler(
                tasks_total=stats.total,
                tasks_done=stats.done,
                tasks_failed=stats.failed,
                tasks_running=stats.running,
                tasks_blocked=stats.blocked,
                tasks_pending=stats.pending + stats.ready,
                current_task_id=self.machine.state.current_task_id,
            )
            self.dashboard.update()
            self.terminal.print_state(self.machine.state)

        # Final UAT for plan-level execution (if configured)
        if not scheduler.has_failures():
            final_uat_ok = await self._final_uat_step(dag)
            if not final_uat_ok:
                self._transition_state(
                    OrchestratorState.FAILED,
                    error_message="Final UAT failed",
                    error_context={"run_id": self.machine.state.run_id},
                )
                return False
        else:
            self._transition_state(
                OrchestratorState.FAILED,
                error_message="One or more tasks failed",
                error_context={"run_id": self.machine.state.run_id},
            )
            return False

        self._transition_state(OrchestratorState.DONE)
        return True

    async def _execute_task(
        self,
        task,
        scheduler: DAGScheduler,
        workdir: Path | None = None,
    ) -> bool:
        """Execute a single task.

        Args:
            task: ParsedTask
            scheduler: DAGScheduler
            workdir: Optional working directory (uses repo root if not specified)

        Returns:
            True if task succeeded
        """
        task_id = task.task_id
        workdir = workdir or self.config.repo.root
        logger.info(f"Executing task: {task_id} - {task.title} (in {workdir})")
        self.terminal.print_progress(f"Task {task_id}: Development started ({task.title})")

        # Create task branch (in worktree if provided)
        branch_name = f"autopilot/{task_id}"
        if workdir != self.config.repo.root:
            # Create branch in worktree's git context
            manager = SubprocessManager(timeout_sec=30)
            result = await manager.run(
                ["git", "checkout", "-B", branch_name],
                cwd=workdir,
            )
            if not result["success"]:
                logger.error(f"Failed to create branch in worktree: {result['output']}")
                return False
        else:
            await self.git_ops.create_branch(branch_name)

        # Update state
        self.machine.update_task(
            task_id,
            title=task.title,
            status="running",
            branch_name=branch_name,
        )

        # Main loop
        max_iterations = self.config.loop.max_iterations
        iterations_used = 0
        last_validation_summary = ""
        task_converged = False
        for iteration in range(max_iterations):
            iterations_used = iteration + 1
            logger.info(f"Iteration {iterations_used}/{max_iterations}")

            # Build
            self.terminal.print_progress(f"Task {task_id}: Coding (iteration {iterations_used})")
            build_success = await self._build_step(
                task_id,
                task,
                workdir=workdir,
                build_context=last_validation_summary,
            )
            if not build_success:
                self.terminal.print_progress(f"Task {task_id}: Failed during coding")
                self.machine.update_task(task_id, status="failed")
                return False

            # Validate
            self.terminal.print_progress(f"Task {task_id}: Updating (validation)")
            validate_success, validation_results, validation_summary = await self._validate_step(
                task_id, task, workdir=workdir
            )
            if not validate_success:
                self.terminal.print_progress(f"Task {task_id}: Updating (fix loop)")
                last_validation_summary = validation_summary
                # Feed into FIX loop
                continue
            last_validation_summary = ""

            # Review
            self.terminal.print_progress(f"Task {task_id}: Review")
            review_success, review_feedback = await self._review_step(
                task_id, task, validation_results, workdir=workdir
            )
            if not review_success:
                self.terminal.print_progress(f"Task {task_id}: Updating (review changes)")
                if review_feedback:
                    last_validation_summary = f"## Review Feedback\n{review_feedback}\n"
                # Feed into FIX loop
                continue
            last_validation_summary = ""

            # Generate UAT cases (if configured)
            self.terminal.print_progress(f"Task {task_id}: UAT generation")
            uat_gen_success = await self._generate_uat_step(task_id, task, workdir=workdir)
            if not uat_gen_success:
                logger.warning("UAT generation failed, continuing with existing UAT")

            # UAT
            self.terminal.print_progress(f"Task {task_id}: UAT run")
            uat_success = await self._uat_step(task_id, task, workdir=workdir)
            if not uat_success:
                self.terminal.print_progress(f"Task {task_id}: Updating (UAT fixes)")
                # Feed into FIX loop
                continue

            # Task complete
            task_converged = True
            break

        if not task_converged:
            logger.error("Task %s did not converge after %s iterations", task_id, max_iterations)
            self.terminal.print_progress(f"Task {task_id}: Failed (max iterations)")
            self.machine.update_task(task_id, status="failed")
            return False

        # Commit changes (in worktree if applicable)
        commit_ok = await self._commit_step(task_id, task, workdir=workdir)
        if not commit_ok:
            self.machine.update_task(task_id, status="failed")
            return False

        # If using worktree, merge branch back to main
        if workdir and workdir != self.config.repo.root:
            logger.info("Merging worktree changes back to main branch")
            try:
                async with self._merge_lock:
                    merge_allowed_paths = None
                    if task.allowed_paths:
                        merge_allowed_paths = [
                            p.strip() for p in task.allowed_paths if (p or "").strip()
                        ]
                        if "tests/uat/" not in merge_allowed_paths and "tests/uat" not in merge_allowed_paths:
                            merge_allowed_paths.append("tests/uat/")
                    await self._merge_worktree_to_main(
                        workdir,
                        branch_name,
                        allowed_paths=merge_allowed_paths,
                        task_id=task_id,
                    )
            except Exception as e:
                logger.error(f"Failed to merge worktree changes: {e}")
                self.machine.update_task(task_id, status="failed")
                return False

        # Push if GitHub enabled (only from main repo)
        if self.github and workdir == self.config.repo.root:
            await self._push_step(task_id, task, iterations_used)

        self.machine.update_task(task_id, status="done")
        self.terminal.print_progress(f"Task {task_id}: Completed")
        return True

    def _format_task_context(self, task, phase: str) -> str:
        """Format a concise task context for agent prompts."""
        lines = [
            f"Task ID: {task.task_id}",
            f"Title: {task.title}",
            f"Phase: {phase}",
        ]
        if task.allowed_paths:
            lines.append(f"Allowed Paths: {', '.join(task.allowed_paths)}")
        if task.constraints:
            lines.append(f"Constraints: {', '.join(task.constraints)}")
        if task.acceptance_criteria:
            lines.append("Acceptance Criteria:")
            lines.extend([f"- {item}" for item in task.acceptance_criteria])
        return "\n".join(lines)

    async def _ensure_worktree_gitdir(self, workdir: Path) -> None:
        """Repair accidental nested git repos inside Autopilot worktrees.

        Models can sometimes run `git init` inside a worktree, replacing the worktree's `.git`
        file (a pointer) with a `.git/` directory. That breaks later `git` commands because they
        target the nested repo instead of the real worktree gitdir.
        """
        if workdir == self.config.repo.root:
            return

        git_marker = workdir / ".git"
        if not git_marker.exists() or not git_marker.is_dir():
            return

        worktrees_dir = self.config.repo.root / ".git" / "worktrees"
        expected_gitdir: Path | None = None

        if worktrees_dir.exists():
            target = str(git_marker)
            for entry in worktrees_dir.iterdir():
                try:
                    gitdir_file = entry / "gitdir"
                    if gitdir_file.is_file() and gitdir_file.read_text().strip() == target:
                        expected_gitdir = entry
                        break
                except Exception:
                    continue

        if expected_gitdir is None:
            candidate = worktrees_dir / workdir.name
            if candidate.exists():
                expected_gitdir = candidate

        if expected_gitdir is None:
            raise Exception(f"Worktree gitdir not found for {workdir}")

        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        backup = workdir / ".autopilot" / "artifacts" / "corrupted-git" / timestamp / "git"
        backup.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(git_marker), str(backup))
        except Exception:
            shutil.rmtree(git_marker, ignore_errors=True)

        git_marker.write_text(f"gitdir: {expected_gitdir}\n", encoding="utf-8")
        logger.warning("Repaired nested .git repo in worktree %s (backup: %s)", workdir, backup)

    async def _get_merge_conflicts(self, repo_root: Path) -> list[str]:
        """Return list of merge conflict paths."""
        manager = SubprocessManager(timeout_sec=30)
        result = await manager.run(
            ["git", "diff", "--name-only", "--diff-filter=U"],
            cwd=repo_root,
        )
        if not result["success"]:
            return []
        return [line.strip() for line in result["output"].splitlines() if line.strip()]

    def _all_conflicts_auto_resolvable(self, conflicts: list[str]) -> bool:
        """Check if conflicts are safe to auto-resolve."""
        for path in conflicts:
            if path.startswith(self._AUTO_RESOLVE_CONFLICT_PREFIXES):
                continue
            if path.endswith(".log"):
                continue
            return False
        return True

    async def _auto_resolve_conflicts(self, repo_root: Path, conflicts: list[str]) -> None:
        """Resolve conflicts by keeping main branch content."""
        manager = SubprocessManager(timeout_sec=30)
        for path in conflicts:
            await manager.run(["git", "checkout", "--ours", "--", path], cwd=repo_root)
            await manager.run(["git", "add", path], cwd=repo_root)

    async def _resolve_conflicts_with_scope(
        self,
        repo_root: Path,
        conflicts: list[str],
        allowed_paths: list[str],
    ) -> None:
        """Resolve conflicts by scope: keep theirs in-scope, ours out-of-scope."""
        manager = SubprocessManager(timeout_sec=30)
        normalized = [p if p.endswith("/") else f"{p}/" for p in allowed_paths]

        for path in conflicts:
            in_scope = any(path.startswith(prefix) for prefix in normalized)
            if in_scope:
                await manager.run(["git", "checkout", "--theirs", "--", path], cwd=repo_root)
            else:
                await manager.run(["git", "checkout", "--ours", "--", path], cwd=repo_root)
            await manager.run(["git", "add", path], cwd=repo_root)

    @staticmethod
    def _extract_merge_untracked_overwrite_paths(output: str) -> list[str]:
        """Extract untracked paths from git's 'would be overwritten by merge' error."""
        lines = output.splitlines()
        paths: list[str] = []
        collecting = False
        for line in lines:
            if "untracked working tree files would be overwritten by merge" in line:
                collecting = True
                continue
            if not collecting:
                continue
            if not line.strip():
                continue
            if line.lstrip() != line:
                paths.append(line.strip())
                continue
            # Stop once we hit the next non-indented line.
            break
        return paths

    @staticmethod
    def _extract_merge_tracked_overwrite_paths(output: str) -> list[str]:
        """Extract tracked paths from git's 'local changes would be overwritten by merge' error."""
        lines = output.splitlines()
        paths: list[str] = []
        collecting = False
        for line in lines:
            if "Your local changes to the following files would be overwritten by merge" in line:
                collecting = True
                continue
            if not collecting:
                continue
            if not line.strip():
                continue
            if line.lstrip() != line:
                paths.append(line.strip())
                continue
            break
        return paths

    @staticmethod
    def _is_ignorable_untracked_path(path: str) -> bool:
        basename = Path(path).name
        return basename in {".DS_Store", "Thumbs.db", "desktop.ini"}

    async def _handle_merge_untracked_overwrite(
        self,
        repo_root: Path,
        paths: list[str],
        allowed_paths: list[str],
    ) -> None:
        """Handle untracked files that block a merge by moving them aside safely."""
        if not paths:
            return

        manager = SubprocessManager(timeout_sec=30)
        normalized_allowed = [p if p.endswith("/") else f"{p}/" for p in allowed_paths]

        to_clean: list[str] = []
        to_backup: list[str] = []
        for path in paths:
            if self._is_ignorable_untracked_path(path):
                to_clean.append(path)
                continue
            if normalized_allowed and any(path.startswith(prefix) for prefix in normalized_allowed):
                to_clean.append(path)
                continue
            to_backup.append(path)

        if to_clean:
            await manager.run(["git", "clean", "-fd", "--", *to_clean], cwd=repo_root)

        if to_backup:
            timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
            backup_root = repo_root / ".autopilot" / "merge-backups" / timestamp
            for rel in to_backup:
                src = repo_root / rel
                if not src.exists():
                    continue
                dst = backup_root / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
                logger.warning("Moved untracked file blocking merge: %s -> %s", rel, dst)

    async def _scoped_apply_branch(
        self,
        repo_root: Path,
        branch_name: str,
        allowed_paths: list[str],
    ) -> bool:
        """Apply branch changes limited to allowed_paths onto the current branch.

        This is a pragmatic fallback when a full merge is blocked by unrelated working tree state
        (e.g. local changes in out-of-scope files).
        """
        if not allowed_paths:
            raise Exception("Scoped apply requires allowed_paths")

        manager = SubprocessManager(timeout_sec=60)

        normalized_allowed = []
        for raw in allowed_paths:
            path = (raw or "").strip().rstrip("/")
            if not path:
                continue
            normalized_allowed.append(path)

        def in_scope(path: str) -> bool:
            if not normalized_allowed:
                return True
            return any(path == prefix or path.startswith(prefix + "/") for prefix in normalized_allowed)

        # Derive the minimal set of paths to apply by diffing branch_name against HEAD, then
        # filtering to allowed_paths. This avoids pathspec failures when allowed paths don't
        # exist in the branch and keeps the blast radius limited to actual changes.
        diff = await manager.run(
            ["git", "diff", "--name-status", "--find-renames", f"HEAD..{branch_name}"],
            cwd=repo_root,
        )
        if not diff.get("success"):
            raise Exception(f"Failed to diff against {branch_name}: {diff.get('output', '')}")

        checkout_paths: list[str] = []
        delete_paths: list[str] = []
        for raw in (diff.get("output") or "").splitlines():
            if not raw.strip():
                continue
            parts = raw.split("\t")
            if not parts:
                continue
            status = parts[0].strip()
            if not status:
                continue
            code = status[0]
            if code == "R" and len(parts) >= 3:
                old = parts[1].strip()
                new = parts[2].strip()
                if new and in_scope(new):
                    checkout_paths.append(new)
                if old and old != new and in_scope(old):
                    delete_paths.append(old)
                continue
            if len(parts) < 2:
                continue
            path = parts[1].strip()
            if not path or not in_scope(path):
                continue
            if code == "D":
                delete_paths.append(path)
            else:
                checkout_paths.append(path)

        checkout_paths = sorted(set(checkout_paths))
        delete_paths = sorted(set(delete_paths))

        if not checkout_paths and not delete_paths:
            logger.info("No in-scope changes found on %s; skipping scoped apply", branch_name)
            return False

        if checkout_paths:
            checkout = await manager.run(
                ["git", "checkout", branch_name, "--", *checkout_paths],
                cwd=repo_root,
            )
        else:
            checkout = {"success": True, "output": ""}

        if not checkout["success"]:
            # If local changes in the allowed paths block checkout, force the checkout for those
            # paths only. This keeps the blast radius limited to the task scope.
            if checkout_paths:
                checkout = await manager.run(
                    ["git", "checkout", "-f", branch_name, "--", *checkout_paths],
                    cwd=repo_root,
                )
                if not checkout["success"]:
                    raise Exception(f"Scoped checkout failed: {checkout['output']}")

        if delete_paths:
            await manager.run(["git", "rm", "-f", "--", *delete_paths], cwd=repo_root)

        staged = await manager.run(["git", "diff", "--cached", "--name-only"], cwd=repo_root)
        if staged["success"] and not staged["output"].strip():
            if checkout_paths:
                await manager.run(["git", "add", "--", *checkout_paths], cwd=repo_root)
            staged = await manager.run(["git", "diff", "--cached", "--name-only"], cwd=repo_root)

        if staged["success"] and not staged["output"].strip():
            logger.info("No in-scope changes to apply for %s; skipping scoped merge", branch_name)
            return False

        commit_result = await manager.run(
            ["git", "commit", "--no-verify", "-m", f"Merge {branch_name} (scoped apply)"],
            cwd=repo_root,
        )
        if not commit_result["success"]:
            raise Exception(f"Scoped merge commit failed: {commit_result['output']}")
        return True

    async def _stash_current_task_uat(self, repo_root: Path, task_id: str) -> bool:
        """Stash the current task's UAT files before merge.

        Stashes tests/uat/test_{task_id}_uat.py and tests/uat/{task_id}_uat.md
        to prevent merge conflicts with UAT files from other tasks.

        Args:
            repo_root: Path to git repository root
            task_id: Current task ID (e.g., "task-1")

        Returns:
            True if stash was created, False if no UAT files to stash
        """
        uat_py = repo_root / "tests" / "uat" / f"test_{task_id}_uat.py"
        uat_md = repo_root / "tests" / "uat" / f"{task_id}_uat.md"

        # Check if UAT files exist
        has_uat_files = uat_py.exists() or uat_md.exists()
        if not has_uat_files:
            logger.debug(f"No UAT files to stash for {task_id}")
            return False

        # Create stash with specific message for later retrieval
        stash_msg = f"autopilot-uat-{task_id}"
        uat_py_rel = str(uat_py.relative_to(repo_root))
        uat_md_rel = str(uat_md.relative_to(repo_root))

        manager = SubprocessManager(timeout_sec=30)
        result = await manager.run(
            ["git", "stash", "push", "-m", stash_msg, "--", uat_py_rel, uat_md_rel],
            cwd=repo_root,
        )

        if not result["success"]:
            logger.warning(f"Failed to stash UAT files for {task_id}: {result['output']}")
            return False

        logger.info(f"Stashed UAT files for {task_id}: {stash_msg}")
        return True

    async def _drop_uat_stash(self, repo_root: Path, task_id: str) -> None:
        """Drop the UAT stash after successful merge.

        After a successful merge, the UAT files are already committed to main,
        so we can safely drop the stash.

        Args:
            repo_root: Path to git repository root
            task_id: Current task ID (e.g., "task-1")
        """
        manager = SubprocessManager(timeout_sec=30)

        # List stashes to find the one we created
        result = await manager.run(["git", "stash", "list"], cwd=repo_root)

        if not result["success"]:
            logger.warning(f"Failed to list stashes: {result['output']}")
            return

        # Find stash by message
        stash_msg = f"autopilot-uat-{task_id}"
        stash_ref = None

        for line in result["output"].splitlines():
            if stash_msg in line:
                # Extract stash ref (e.g., "stash@{0}")
                stash_ref = line.split(":")[0].strip()
                break

        if not stash_ref:
            logger.debug(f"No UAT stash found for {task_id}")
            return

        # Drop the stash
        result = await manager.run(["git", "stash", "drop", stash_ref], cwd=repo_root)

        if not result["success"]:
            logger.warning(f"Failed to drop UAT stash {stash_ref}: {result['output']}")
            return

        logger.info(f"Dropped UAT stash for {task_id}: {stash_ref}")

    async def _merge_worktree_to_main(
        self,
        worktree_path: Path,
        branch_name: str,
        allowed_paths: list[str] | None = None,
        task_id: str | None = None,
    ) -> None:
        """Merge worktree branch back to main branch.

        Args:
            worktree_path: Path to worktree
            branch_name: Branch name to merge
            allowed_paths: Optional list of allowed paths for scoped merge
            task_id: Optional task ID for UAT file stashing
        """
        manager = SubprocessManager(timeout_sec=60)

        # Merge from repo root to avoid worktree checkout conflicts
        repo_root = self.config.repo.root
        default_branch = self.config.repo.default_branch
        current = await manager.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_root)
        if not current["success"]:
            raise Exception(f"Failed to get current branch: {current['output']}")
        current_branch = current["output"].strip()
        if current_branch != default_branch:
            result = await manager.run(["git", "checkout", default_branch], cwd=repo_root)
            if not result["success"]:
                # If the configured default branch doesn't exist (common on fresh repos where
                # Git still uses `master`), fall back to the current branch instead of failing.
                if "pathspec" in (result["output"] or "") and default_branch in (
                    result["output"] or ""
                ):
                    logger.warning(
                        "Default branch %s not found; staying on %s for merge",
                        default_branch,
                        current_branch,
                    )
                    default_branch = current_branch
                else:
                    raise Exception(f"Failed to checkout default branch: {result['output']}")

        # Stash current task's UAT files before merge to prevent conflicts
        # with UAT files from other tasks that may already be in main
        if task_id:
            logger.info(f"Stashing UAT files for {task_id} before merge")
            await self._stash_current_task_uat(repo_root, task_id)

        # Merge the task branch into default branch
        result = await manager.run(
            ["git", "merge", "--no-ff", "--no-verify", branch_name],
            cwd=repo_root,
        )
        if not result["success"]:
            conflicts = await self._get_merge_conflicts(repo_root)
            if conflicts and self._all_conflicts_auto_resolvable(conflicts):
                await self._auto_resolve_conflicts(repo_root, conflicts)
                commit_result = await manager.run(
                    [
                        "git",
                        "commit",
                        "--no-verify",
                        "-m",
                        f"Merge {branch_name} (auto-resolved logs)",
                    ],
                    cwd=repo_root,
                )
                if not commit_result["success"]:
                    if task_id:
                        logger.warning(f"Merge failed for {task_id}, UAT stash preserved for inspection")
                    raise Exception(f"Failed to finalize auto-merge: {commit_result['output']}")
            elif conflicts and allowed_paths:
                await self._resolve_conflicts_with_scope(repo_root, conflicts, allowed_paths)
                commit_result = await manager.run(
                    ["git", "commit", "--no-verify", "-m", f"Merge {branch_name} (scope-resolved)"],
                    cwd=repo_root,
                )
                if not commit_result["success"]:
                    if task_id:
                        logger.warning(f"Merge failed for {task_id}, UAT stash preserved for inspection")
                    raise Exception(f"Failed to finalize scope merge: {commit_result['output']}")
            elif "untracked working tree files would be overwritten by merge" in result["output"]:
                blocking_paths = self._extract_merge_untracked_overwrite_paths(result["output"])
                await self._handle_merge_untracked_overwrite(
                    repo_root=repo_root,
                    paths=blocking_paths,
                    allowed_paths=allowed_paths or [],
                )
                retry = await manager.run(
                    ["git", "merge", "--no-ff", "--no-verify", branch_name],
                    cwd=repo_root,
                )
                if not retry["success"]:
                    if task_id:
                        logger.warning(f"Merge failed for {task_id}, UAT stash preserved for inspection")
                    raise Exception(f"Failed to merge branch: {retry['output']}")
            elif (
                "Your local changes to the following files would be overwritten by merge"
                in result["output"]
            ):
                blocking_paths = self._extract_merge_tracked_overwrite_paths(result["output"])
                if allowed_paths and blocking_paths:
                    normalized = [p if p.endswith("/") else f"{p}/" for p in allowed_paths]
                    in_scope_blockers = [
                        p
                        for p in blocking_paths
                        if any(p.startswith(prefix) for prefix in normalized)
                    ]
                    if not in_scope_blockers:
                        applied = await self._scoped_apply_branch(
                            repo_root=repo_root,
                            branch_name=branch_name,
                            allowed_paths=allowed_paths,
                        )
                        if applied:
                            logger.info(
                                "Applied %s onto %s via scoped apply (blocked by local changes: %s)",
                                branch_name,
                                default_branch,
                                ", ".join(blocking_paths),
                            )
                        else:
                            logger.info(
                                "No in-scope changes found on %s; skipping merge (blocked by local changes: %s)",
                                branch_name,
                                ", ".join(blocking_paths),
                            )
                        return

                retry = await manager.run(
                    ["git", "merge", "--no-ff", "--no-verify", "--autostash", branch_name],
                    cwd=repo_root,
                )
                if (
                    not retry["success"]
                    and "untracked working tree files would be overwritten by merge"
                    in retry["output"]
                ):
                    blocking_untracked = self._extract_merge_untracked_overwrite_paths(
                        retry["output"]
                    )
                    await self._handle_merge_untracked_overwrite(
                        repo_root=repo_root,
                        paths=blocking_untracked,
                        allowed_paths=allowed_paths or [],
                    )
                    retry = await manager.run(
                        ["git", "merge", "--no-ff", "--no-verify", "--autostash", branch_name],
                        cwd=repo_root,
                    )
                if not retry["success"]:
                    if task_id:
                        logger.warning(f"Merge failed for {task_id}, UAT stash preserved for inspection")
                    raise Exception(f"Failed to merge branch: {retry['output']}")

        logger.info(f"Merged {branch_name} into {default_branch}")

        # Drop the UAT stash after successful merge since files are now in main
        if task_id:
            logger.info(f"Cleaning up UAT stash for {task_id}")
            await self._drop_uat_stash(repo_root, task_id)

    async def _build_step(
        self,
        task_id: str,
        task,
        workdir: Path | None = None,
        build_context: str = "",
    ) -> bool:
        """Execute build step.

        Args:
            task_id: Task ID
            task: ParsedTask
            workdir: Working directory (uses repo root if not specified)

        Returns:
            True if build succeeded
        """
        logger.info(f"Build step for {task_id}")

        workdir = workdir or self.config.repo.root

        try:
            # Execute builder
            context = self._format_task_context(task, phase="Development/Coding")
            prompt = f"## Task Context\n{context}\n\n{task.raw_content}"
            if build_context:
                prompt = f"{prompt}\n\n## Validation Feedback\n{build_context}\n"
            result = await self.builder.execute(
                prompt=prompt,
                timeout_sec=self.config.loop.build_timeout_sec,
                work_dir=workdir,
            )

            if result["success"]:
                logger.info("Build succeeded")
                return True
            else:
                logger.error(f"Build failed: {result.get('output', '')}")
                return False

        except AgentError as e:
            logger.error(f"Build error: {e}")
            return False

    async def _validate_step(
        self, task_id: str, task, workdir: Path | None = None
    ) -> tuple[bool, dict, str]:
        """Execute validation step.

        Args:
            task_id: Task ID
            task: ParsedTask
            workdir: Working directory (uses repo root if not specified)

        Returns:
            Tuple of (success: bool, results: dict)
        """
        logger.info(f"Validate step for {task_id}")

        workdir = workdir or self.config.repo.root

        runner = ValidationRunner(
            work_dir=workdir,
            timeout_sec=self.config.loop.validate_timeout_sec,
            allow_no_tests=self.config.loop.allow_no_tests,
            venv_dir=self.config.repo.root / ".autopilot" / "venv",
        )

        try:
            def pick(name: str, default: str | None) -> tuple[str | None, bool]:
                override = task.validation_commands.get(name)
                if override is None or override == "":
                    return default, False
                return override, True

            format_cmd, format_override = pick("format", self.config.commands.format)
            lint_cmd, lint_override = pick("lint", self.config.commands.lint)
            tests_cmd, tests_override = pick("tests", self.config.commands.tests)
            uat_cmd, uat_override = pick("uat", self.config.commands.uat)

            defaults = {
                "format": self.config.commands.format,
                "lint": self.config.commands.lint,
                "tests": self.config.commands.tests,
                "uat": self.config.commands.uat,
            }
            used_override = {
                "format": format_override,
                "lint": lint_override,
                "tests": tests_override,
                "uat": uat_override,
            }

            effective_commands = {
                "format": format_cmd,
                "lint": lint_cmd,
                "tests": tests_cmd,
                "uat": uat_cmd,
            }

            results = await runner.run_all(commands=effective_commands)

            # Plans can include per-task validation overrides (e.g. `pytest tests/unit/test_x.py`).
            # If an override is invalid in the current repo (common when the planner guesses a
            # filename), fall back to the repo-level configured command so the run can proceed.
            tests_result = results.get("tests")
            if (
                tests_result
                and not tests_result.success
                and used_override.get("tests")
                and defaults.get("tests")
                and defaults["tests"] != effective_commands.get("tests")
            ):
                logger.warning(
                    "Task %s tests override failed (%s); retrying default tests command",
                    task_id,
                    tests_result.exit_code,
                )
                fallback = await runner.run_tests(defaults["tests"])
                if fallback.success:
                    logger.warning(
                        "Default tests command succeeded; ignoring failing override for task %s",
                        task_id,
                    )
                    results["tests"] = fallback

            # Check if all passed
            all_passed = all(r.success for r in results.values())

            if not all_passed:
                summary = runner.get_failure_summary(results)
                logger.error(f"Validation failed:\n{summary}")
                return False, results, summary

            return True, results, ""

        except Exception as e:
            logger.error(f"Validation error: {e}")
            return False, {}, str(e)

    async def _review_step(
        self, task_id: str, task, validation_results: dict, workdir: Path | None = None
    ) -> tuple[bool, str]:
        """Execute review step.

        Args:
            task_id: Task ID
            task: ParsedTask
            validation_results: Results from validation step
            workdir: Working directory (uses repo root if not specified)

        Returns:
            (success, feedback). feedback is non-empty when changes are requested or an error occurred.
        """
        logger.info(f"Review step for {task_id}")

        workdir = workdir or self.config.repo.root
        await self._ensure_worktree_gitdir(workdir)

        try:
            allowed_paths = [p.strip() for p in (task.allowed_paths or []) if p.strip()]
            manager = SubprocessManager(timeout_sec=30)

            # `git diff` does not show untracked files, but our builders frequently apply patches
            # via `git apply`, which creates new files as untracked. Make new in-scope files
            # visible to review/UAT diff by adding them as "intent-to-add" (without staging
            # content).
            try:
                untracked_cmd = ["git", "ls-files", "--others", "--exclude-standard"]
                if allowed_paths:
                    untracked_cmd.extend(["--", *allowed_paths])
                untracked = await manager.run(untracked_cmd, cwd=workdir)
                if untracked.get("success"):
                    candidates = []
                    for raw in (untracked.get("output") or "").splitlines():
                        path = raw.strip()
                        if not path:
                            continue
                        norm = path.replace("\\", "/")
                        if "/__pycache__/" in norm or norm.endswith(".pyc"):
                            continue
                        if norm.startswith(".autopilot/"):
                            continue
                        candidates.append(path)
                    if candidates:
                        await manager.run(["git", "add", "-N", "--", *candidates], cwd=workdir)
            except Exception:
                # Best-effort; review can proceed without this context if it fails.
                pass

            # Get diff from worktree
            diff_cmd = ["git", "diff"]
            if allowed_paths:
                diff_cmd.extend(["--", *allowed_paths])
            result = await manager.run(diff_cmd, cwd=workdir)
            diff = result["output"]

            # Generate validation output summary
            validation_output = ""
            if validation_results:
                summary = ValidationRunner(
                    work_dir=workdir,
                ).get_failure_summary(validation_results)
                if not all(r.success for r in validation_results.values()):
                    validation_output = summary

            if not diff.strip() and not validation_output:
                logger.info("No changes detected; skipping review")
                return True, ""

            # Reviewers only see the diff hunks, which can omit important file-level context
            # (e.g. existing imports). Provide a small, deterministic snippet of each changed
            # file header to reduce false positives and improve review quality.
            try:
                name_cmd = ["git", "diff", "--name-only"]
                if allowed_paths:
                    name_cmd.extend(["--", *allowed_paths])
                name_res = await manager.run(name_cmd, cwd=workdir)
                changed_files = [
                    line.strip()
                    for line in (name_res.get("output") or "").splitlines()
                    if line.strip()
                ]
                # `git diff --name-only` excludes untracked files, but reviewers need to see new
                # files too (common early in a task). Include a bounded set of untracked files
                # under allowed_paths.
                try:
                    status_res = await manager.run(["git", "status", "--porcelain"], cwd=workdir)
                    untracked: list[str] = []
                    allowed = [p.strip() for p in (allowed_paths or []) if p.strip()]
                    for raw in (status_res.get("output") or "").splitlines():
                        if not raw.startswith("?? "):
                            continue
                        rel = raw[3:].strip()
                        if not rel:
                            continue
                        if allowed:
                            in_allowed = False
                            for prefix in allowed:
                                if prefix.endswith("/"):
                                    if rel.startswith(prefix):
                                        in_allowed = True
                                        break
                                else:
                                    if rel == prefix or rel.startswith(prefix + "/"):
                                        in_allowed = True
                                        break
                            if not in_allowed:
                                continue
                        p = workdir / rel
                        if p.is_file():
                            untracked.append(rel)
                    if untracked:
                        merged = list(dict.fromkeys([*changed_files, *untracked]))
                        changed_files = merged
                except Exception:
                    pass
                snippet_lines = []
                remaining = 12000  # cap extra context size
                head_lines = 160
                tail_lines = 160
                for rel in changed_files[:10]:
                    p = workdir / rel
                    if not p.is_file():
                        continue
                    try:
                        lines = p.read_text(errors="replace").splitlines()
                    except Exception:
                        continue

                    excerpt: list[str] = []
                    excerpt.extend(lines[:head_lines])
                    if len(lines) > head_lines + tail_lines:
                        excerpt.extend(["", "... (snip) ...", ""])
                        excerpt.extend(lines[-tail_lines:])
                        header = f"--- {rel} (head {head_lines} + tail {tail_lines} lines)\n"
                    else:
                        excerpt.extend(lines[head_lines:])
                        header = f"--- {rel} (full file)\n"

                    block = "\n".join(excerpt).rstrip() + "\n"
                    piece = header + block + "\n"
                    if remaining <= 0:
                        break
                    if len(piece) > remaining:
                        piece = piece[:remaining]
                    snippet_lines.append(piece)
                    remaining -= len(piece)
                if snippet_lines:
                    diff = f"{diff}\n\n## File Context\n" + "".join(snippet_lines)
            except Exception:
                # Review context is a best-effort enhancement; never fail the run for it.
                pass

            # Skip review if emergency mode
            if self.safety.should_skip_review():
                logger.warning("⚠️  Skipping review (EMERGENCY MODE)")
                return True, ""

            # Execute reviewer
            context = self._format_task_context(task, phase="Review")
            attempts = max(0, int(getattr(self.config.reviewer, "max_retries", 0))) + 1
            result = None
            for attempt in range(attempts):
                try:
                    result = await self.reviewer.review(
                        diff=diff,
                        validation_output=validation_output,
                        timeout_sec=self.config.loop.review_timeout_sec,
                        work_dir=workdir,
                        context=context,
                    )
                    break
                except AgentError as e:
                    if attempt < attempts - 1:
                        logger.warning(
                            "Review attempt %s/%s failed (%s); retrying",
                            attempt + 1,
                            attempts,
                            e,
                        )
                        await asyncio.sleep(0.5)
                        continue
                    raise

            verdict = result.get("verdict") if isinstance(result, dict) else None
            if verdict == "approve":
                logger.info("Review approved")
                return True, ""
            elif verdict == "request_changes":
                feedback = result.get("feedback", "") if isinstance(result, dict) else ""
                logger.warning("Review requested changes: %s", feedback)
                return False, feedback
            logger.error("Review returned invalid response: %s", result)
            return False, f"Invalid reviewer response: {result}"

        except AgentError as e:
            logger.error(f"Review error: {e}")
            return False, str(e)
        except Exception as e:
            logger.error(f"Review error: {e}")
            return False, str(e)

    async def _generate_uat_step(self, task_id: str, task, workdir: Path | None = None) -> bool:
        """Generate UAT cases as executable Python pytest code.

        Args:
            task_id: Task ID
            task: ParsedTask
            workdir: Working directory (uses repo root if not specified)

        Returns:
            True if generation succeeded (or skipped)
        """
        logger.info(f"UAT generation for {task_id}")

        workdir = workdir or self.config.repo.root
        await self._ensure_worktree_gitdir(workdir)

        # Check if UAT command exists (task override or repo default)
        uat_command = task.validation_commands.get("uat")
        if uat_command is None or uat_command == "":
            uat_command = self.config.commands.uat
        if not uat_command:
            logger.info("No UAT command configured, skipping UAT generation")
            return True

        try:
            allowed_paths = [p.strip() for p in (task.allowed_paths or []) if p.strip()]
            manager = SubprocessManager(timeout_sec=30)
            diff_cmd = ["git", "diff"]
            if allowed_paths:
                diff_cmd.extend(["--", *allowed_paths])
            result = await manager.run(diff_cmd, cwd=workdir)
            diff = result["output"]

            # Generate UAT Python code using Codex
            context = self._format_task_context(task, phase="UAT Generation")
            uat_content = await self.planner.generate_uat(
                task_content=task.raw_content,
                diff=diff,
                timeout_sec=self.config.loop.uat_generate_timeout_sec,
                work_dir=workdir,
                context=context,
            )

            # Normalize content to executable Python
            uat_code = self._normalize_uat_code(uat_content)

            # Write UAT artifacts

            uat_dir = workdir / "tests" / "uat"
            uat_dir.mkdir(parents=True, exist_ok=True)

            # Convert task_id to valid Python identifier
            # Example: "add-auth" -> "test_add_auth_uat.py"
            safe_task_id = task_id.replace("-", "_")
            uat_file = uat_dir / f"test_{safe_task_id}_uat.py"
            uat_md = uat_dir / f"{safe_task_id}_uat.md"

            # Always save raw content for auditing
            with open(uat_md, "w") as f:
                f.write(uat_content)

            # Ensure we have executable tests; otherwise create a skipped test
            if "def test_" not in uat_code:
                uat_code = "\n".join(
                    [
                        "import pytest",
                        "",
                        "pytest.skip(",
                        f'    "UAT cases generated at {uat_md} (no executable tests produced)",',
                        "    allow_module_level=True,",
                        ")",
                    ]
                )
            else:
                # Guard against invalid Python (e.g. placeholder identifiers copied verbatim).
                try:
                    compile(uat_code, str(uat_file), "exec")
                except SyntaxError as e:
                    logger.warning("Generated UAT code is invalid (%s); writing skip file", e)
                    uat_code = "\n".join(
                        [
                            "import pytest",
                            "",
                            "pytest.skip(",
                            f'    "Invalid UAT code generated at {uat_md} ({e.__class__.__name__}); see raw file.",',
                            "    allow_module_level=True,",
                            ")",
                        ]
                    )

            with open(uat_file, "w") as f:
                f.write(uat_code)

            # Ensure generated UAT doesn't break future lint runs (these files are committed and
            # validated in subsequent tasks). Best-effort: auto-fix + format just the generated
            # file when ruff is available via our validation runner.
            try:
                fix_runner = ValidationRunner(
                    work_dir=workdir,
                    timeout_sec=min(self.config.loop.validate_timeout_sec, 180),
                    allow_no_tests=self.config.loop.allow_no_tests,
                    venv_dir=self.config.repo.root / ".autopilot" / "venv",
                )
                await fix_runner.run_lint(f"ruff check --fix {uat_file}")
                await fix_runner.run_format(f"ruff format {uat_file}")
                check = await fix_runner.run_lint(f"ruff check {uat_file}")
                if not check.success:
                    logger.warning(
                        "Generated UAT still fails ruff; replacing with a skipped test to avoid breaking lint: %s",
                        uat_file,
                    )
                    uat_code = "\n".join(
                        [
                            "# ruff: noqa",
                            "import pytest",
                            "",
                            "pytest.skip(",
                            f'    "UAT generation produced code that fails lint; see raw file at {uat_md}.",',
                            "    allow_module_level=True,",
                            ")",
                        ]
                    )
                    with open(uat_file, "w") as f:
                        f.write(uat_code)
            except Exception as e:
                logger.warning("Failed to auto-fix generated UAT with ruff: %s", e)

            logger.info(f"UAT generated: {uat_file} (raw: {uat_md})")
            return True

        except AgentError as e:
            logger.error(f"UAT generation error: {e}")
            return False

    async def _uat_step(self, task_id: str, task, workdir: Path | None = None) -> bool:
        """Execute UAT step.

        Args:
            task_id: Task ID
            task: ParsedTask
            workdir: Working directory (uses repo root if not specified)

        Returns:
            True if UAT passed (or skipped)
        """
        logger.info(f"UAT step for {task_id}")

        workdir = workdir or self.config.repo.root

        runner = ValidationRunner(
            work_dir=workdir,
            timeout_sec=self.config.loop.uat_run_timeout_sec,
            allow_no_tests=self.config.loop.allow_no_tests,
            venv_dir=self.config.repo.root / ".autopilot" / "venv",
        )

        try:
            uat_command = task.validation_commands.get("uat")
            if uat_command is None or uat_command == "":
                uat_command = self.config.commands.uat
            if not uat_command:
                logger.info("No UAT command configured, skipping")
                return True

            result = await runner.run_uat(uat_command)

            if result.success:
                logger.info("UAT passed")
                return True
            else:
                logger.error(f"UAT failed: {result.output}")
                return False

        except Exception as e:
            logger.error(f"UAT error: {e}")
            return False

    async def _final_uat_step(self, dag: TaskDAG) -> bool:
        """Run final UAT after all tasks complete (plan-level)."""
        uat_command = self.config.commands.uat
        if not uat_command:
            logger.info("No UAT command configured, skipping final UAT")
            return True

        logger.info("Running final UAT for completed DAG")
        self._transition_state(OrchestratorState.FINAL_UAT_GENERATE)

        # Generate final UAT cases from combined tasks + diff
        try:
            combined_tasks = "\n\n".join([t.raw_content for t in dag.tasks.values()])
            diff = await self.git_ops.get_diff()
            uat_content = await self.planner.generate_uat(
                task_content=combined_tasks,
                diff=diff,
                timeout_sec=self.config.orchestrator.final_uat_timeout_sec,
                work_dir=self.config.repo.root,
                context="Final UAT generation for completed DAG",
            )

            uat_code = self._normalize_uat_code(uat_content)
            from pathlib import Path

            uat_dir = Path("tests/uat")
            uat_dir.mkdir(parents=True, exist_ok=True)
            uat_md = uat_dir / "final_uat.md"
            uat_py = uat_dir / "test_final_uat.py"
            with open(uat_md, "w") as f:
                f.write(uat_content)
            if "def test_" not in uat_code:
                uat_code = "\n".join(
                    [
                        "import pytest",
                        "",
                        "pytest.skip(",
                        '    "Final UAT generated as markdown (no executable tests produced)",',
                        "    allow_module_level=True,",
                        ")",
                    ]
                )
            with open(uat_py, "w") as f:
                f.write(uat_code)

            # Best-effort: keep generated artifacts compatible with common lint gates.
            try:
                fix_runner = ValidationRunner(
                    work_dir=self.config.repo.root,
                    timeout_sec=min(self.config.orchestrator.final_uat_timeout_sec, 180),
                    venv_dir=self.config.repo.root / ".autopilot" / "venv",
                )
                await fix_runner.run_lint(f"ruff check --fix {uat_py}")
                await fix_runner.run_format(f"ruff format {uat_py}")
                check = await fix_runner.run_lint(f"ruff check {uat_py}")
                if not check.success:
                    logger.warning(
                        "Final UAT still fails ruff; replacing with a skipped test to avoid breaking lint: %s",
                        uat_py,
                    )
                    safe = "\n".join(
                        [
                            "# ruff: noqa",
                            "import pytest",
                            "",
                            "pytest.skip(",
                            '    "Final UAT generated as markdown (no lint-clean executable tests produced)",',
                            "    allow_module_level=True,",
                            ")",
                        ]
                    )
                    with open(uat_py, "w") as f:
                        f.write(safe)
            except Exception as e:
                logger.warning("Failed to auto-fix final UAT with ruff: %s", e)
        except Exception as e:
            logger.warning(f"Final UAT generation failed: {e}")

        # Run UAT command
        self._transition_state(OrchestratorState.FINAL_UAT_RUN)
        runner = ValidationRunner(
            work_dir=self.config.repo.root,
            timeout_sec=self.config.orchestrator.final_uat_timeout_sec,
            venv_dir=self.config.repo.root / ".autopilot" / "venv",
        )
        try:
            result = await runner.run_uat(uat_command)
            if result.success:
                logger.info("Final UAT passed")
                return True
            logger.error(f"Final UAT failed: {result.output}")
            return False
        except Exception as e:
            logger.error(f"Final UAT error: {e}")
            return False

    @staticmethod
    def _normalize_uat_code(content: str) -> str:
        """Normalize generated UAT content into executable Python."""
        text = content.strip()
        if "```" not in text:
            return text

        fence_start = text.find("```")
        if fence_start == -1:
            return text
        fence_end = text.find("```", fence_start + 3)
        if fence_end == -1:
            return text

        code_block = text[fence_start + 3 : fence_end]
        stripped = code_block.lstrip()
        if stripped.startswith("python"):
            stripped = stripped[len("python") :]
        return stripped.strip()

    async def _commit_step(self, task_id: str, task, workdir: Path | None = None) -> bool:
        """Commit changes.

        Args:
            task_id: Task ID
            task: ParsedTask
            workdir: Working directory (uses repo root if not specified)

        Returns:
            True if commit succeeded (or there was nothing to commit).
        """
        logger.info(f"Commit step for {task_id}")

        workdir = workdir or self.config.repo.root
        await self._ensure_worktree_gitdir(workdir)

        allowed_paths = [p.strip() for p in (task.allowed_paths or []) if p.strip()]
        manager = SubprocessManager(timeout_sec=30)

        # Autopilot generates per-task UAT artifacts under tests/uat/. These should be considered
        # in-scope for the task even when the task's allowed_paths is restricted (e.g. src-only).
        # Otherwise, runs fail at commit time with "Out-of-scope changes" despite the artifacts
        # being produced by the orchestrator.
        uat_root = "tests/uat"
        uat_root_prefix = f"{uat_root}/"
        safe_task_id = task_id.replace("-", "_")
        uat_artifacts = {
            f"tests/uat/test_{safe_task_id}_uat.py",
            f"tests/uat/{safe_task_id}_uat.md",
        }

        # Housekeeping: remove known-noise paths from tracking and clean ignored files.
        await manager.run(["git", "rm", "-r", "--cached", "--ignore-unmatch", "logs"], cwd=workdir)
        await manager.run(["git", "clean", "-fdX", "-e", ".autopilot/venv/"], cwd=workdir)

        # Remove common Python cache artifacts within allowed paths so we don't accidentally
        # commit them in repos that lack a proper .gitignore.
        try:
            allowed_dirs: list[Path] = []
            for raw in allowed_paths:
                prefix = (raw or "").strip().rstrip("/")
                if not prefix:
                    continue
                candidate = workdir / prefix
                if candidate.is_dir():
                    allowed_dirs.append(candidate)
                else:
                    parent = candidate.parent
                    if parent.is_dir() and parent != workdir:
                        allowed_dirs.append(parent)

            for base in allowed_dirs:
                for cache_dir in base.rglob("__pycache__"):
                    if cache_dir.is_dir():
                        shutil.rmtree(cache_dir, ignore_errors=True)
                for pattern in ("*.pyc", "*.pyo"):
                    for bytecode in base.rglob(pattern):
                        try:
                            bytecode.unlink()
                        except Exception:
                            pass
        except Exception:
            pass

        # Stage per-task UAT artifacts early so `git status` doesn't report `tests/uat/` as a
        # single untracked directory (which would otherwise look out-of-scope).
        uat_dir = workdir / uat_root
        if uat_dir.exists():
            await manager.run(["git", "add", "--", uat_root], cwd=workdir)
        else:
            for rel in sorted(uat_artifacts):
                if (workdir / rel).exists():
                    await manager.run(["git", "add", "--", rel], cwd=workdir)

        # Stage in-scope paths early so `git status` doesn't collapse nested untracked content
        # into a single parent directory entry (e.g. `?? src/`), which can incorrectly appear
        # out-of-scope when allowed_paths is a subdirectory like `src/taskpilot/`.
        if allowed_paths:
            early_add: list[str] = []
            for raw in allowed_paths:
                path = (raw or "").strip()
                if not path:
                    continue
                probe = workdir / path.rstrip("/")
                if probe.exists():
                    early_add.append(path)
            if early_add:
                await manager.run(["git", "add", "--", *early_add], cwd=workdir)

        status = await manager.run(["git", "status", "--porcelain"], cwd=workdir)
        if not status["success"]:
            logger.error("Failed to get git status: %s", status["output"])
            return False

        def extract_path(line: str) -> str:
            payload = line[3:].strip()
            if " -> " in payload:
                payload = payload.split(" -> ", 1)[1].strip()
            return payload

        def in_scope(path: str) -> bool:
            if path == uat_root or path == uat_root_prefix or path.startswith(uat_root_prefix):
                return True
            if path in uat_artifacts:
                return True
            if not allowed_paths:
                return True
            # `git status --porcelain` can report untracked directories with a trailing slash.
            # Treat a directory as in-scope if it is a parent of any allowed path.
            if path.endswith("/"):
                for prefix in allowed_paths:
                    if prefix and prefix.startswith(path):
                        return True
            for prefix in allowed_paths:
                if prefix.endswith("/"):
                    if path.startswith(prefix):
                        return True
                else:
                    if path == prefix or path.startswith(prefix + "/"):
                        return True
            return False

        def is_noise(path: str) -> bool:
            return (
                path.startswith(".autopilot/")
                or path.startswith("logs/")
                or "/__pycache__/" in f"/{path}/"
                or path.endswith((".pyc", ".pyo"))
                or path.startswith(".pytest_cache/")
                or path.endswith("/.DS_Store")
                or path == ".DS_Store"
            )

        violations: list[str] = []
        violation_codes: dict[str, str] = {}
        for line in status["output"].splitlines():
            if not line.strip():
                continue
            code = line[:2]
            path = extract_path(line)
            if not path:
                continue
            if is_noise(path):
                continue
            if not in_scope(path):
                violations.append(path)
                violation_codes[path] = code

        if violations:
            # Heuristic fix: if the agent created a new root-level markdown artifact outside
            # allowed paths, move it into the first allowed directory. This keeps strict scoping
            # while preventing common "wrote docs to repo root" failures.

            def pick_primary_allowed_dir() -> str | None:
                for prefix in allowed_paths:
                    prefix = prefix.strip()
                    if not prefix:
                        continue
                    if prefix.endswith("/"):
                        return prefix.rstrip("/")
                    candidate = workdir / prefix
                    if candidate.is_dir():
                        return prefix.rstrip("/")
                    # If the allowed path is a file (e.g. "frontend/FOO.md"), fall back to the
                    # containing directory so we can relocate out-of-scope root docs into scope.
                    parent = candidate.parent
                    if parent != workdir:
                        try:
                            rel = parent.relative_to(workdir)
                        except Exception:
                            continue
                        if str(rel) != ".":
                            return str(rel)
                return None

            moved = False
            primary_dir = pick_primary_allowed_dir()
            root_md_violations = [
                p for p in violations if "/" not in p and p.lower().endswith(".md")
            ]

            if primary_dir and root_md_violations:
                target_dir = workdir / primary_dir
                target_dir.mkdir(parents=True, exist_ok=True)

                timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
                backup_root = workdir / ".autopilot" / "artifacts" / "out-of-scope" / timestamp

                for path in root_md_violations:
                    src = workdir / path
                    if not src.exists():
                        continue

                    dst_rel = f"{primary_dir}/{path}"
                    dst = workdir / dst_rel
                    dst.parent.mkdir(parents=True, exist_ok=True)

                    code = violation_codes.get(path) or ""
                    is_tracked = code != "??"

                    if not dst.exists():
                        if is_tracked:
                            mv_res = await manager.run(
                                ["git", "mv", "--", path, dst_rel],
                                cwd=workdir,
                            )
                            if not mv_res["success"]:
                                shutil.move(str(src), str(dst))
                        else:
                            shutil.move(str(src), str(dst))

                        logger.warning(
                            "Moved out-of-scope doc into scope for %s: %s -> %s",
                            task_id,
                            path,
                            dst_rel,
                        )
                        moved = True
                        continue

                    # Destination already exists; preserve a copy and remove/revert the out-of-scope
                    # doc so scope guards can proceed.
                    try:
                        backup_root.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(str(src), str(backup_root / path))
                    except Exception:
                        pass

                    if is_tracked:
                        restore_res = await manager.run(
                            ["git", "restore", "--staged", "--worktree", "--", path],
                            cwd=workdir,
                        )
                        if not restore_res["success"]:
                            await manager.run(["git", "checkout", "--", path], cwd=workdir)
                    else:
                        try:
                            src.unlink()
                        except Exception:
                            pass

                    logger.warning(
                        "Backed up and removed out-of-scope doc for %s: %s -> %s",
                        task_id,
                        path,
                        backup_root / path,
                    )
                    moved = True

            if moved:
                status = await manager.run(["git", "status", "--porcelain"], cwd=workdir)
                if not status["success"]:
                    logger.error("Failed to get git status after moving docs: %s", status["output"])
                    return False

                violations = []
                violation_codes = {}
                for line in status["output"].splitlines():
                    if not line.strip():
                        continue
                    code = line[:2]
                    path = extract_path(line)
                    if not path or is_noise(path):
                        continue
                    if not in_scope(path):
                        violations.append(path)
                        violation_codes[path] = code

            if violations:
                # As a final fallback, keep task scope strict by removing/reverting out-of-scope
                # changes instead of failing the entire run. Preserve a copy under
                # .autopilot/artifacts/out-of-scope for debugging.
                timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
                backup_root = (
                    workdir / ".autopilot" / "artifacts" / "out-of-scope" / task_id / timestamp
                )
                try:
                    backup_root.mkdir(parents=True, exist_ok=True)
                except Exception:
                    pass

                for path in sorted(set(violations)):
                    src = workdir / path
                    code = violation_codes.get(path) or ""

                    # Untracked files/dirs: move aside.
                    if code == "??":
                        if src.exists():
                            dst = backup_root / path
                            dst.parent.mkdir(parents=True, exist_ok=True)
                            try:
                                shutil.move(str(src), str(dst))
                                logger.warning(
                                    "Moved out-of-scope untracked path for %s: %s -> %s",
                                    task_id,
                                    path,
                                    dst,
                                )
                            except Exception as e:
                                logger.error(
                                    "Failed to move out-of-scope path for %s (%s): %s",
                                    task_id,
                                    path,
                                    e,
                                )
                        continue

                    # Tracked modifications: back up current content (best-effort), then restore.
                    if src.exists() and src.is_file():
                        dst = backup_root / path
                        try:
                            dst.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(str(src), str(dst))
                        except Exception:
                            pass

                    restore_res = await manager.run(
                        ["git", "restore", "--staged", "--worktree", "--", path],
                        cwd=workdir,
                    )
                    if not restore_res["success"]:
                        # Older git versions may not support restore in some cases; fall back.
                        await manager.run(["git", "checkout", "--", path], cwd=workdir)

                    logger.warning(
                        "Reverted out-of-scope tracked path for %s: %s (backup: %s)",
                        task_id,
                        path,
                        backup_root / path,
                    )

                # Re-check status after cleanup.
                status = await manager.run(["git", "status", "--porcelain"], cwd=workdir)
                if not status["success"]:
                    logger.error(
                        "Failed to get git status after cleaning out-of-scope paths: %s",
                        status["output"],
                    )
                    return False

                violations = []
                for line in status["output"].splitlines():
                    if not line.strip():
                        continue
                    path = extract_path(line)
                    if not path or is_noise(path):
                        continue
                    if not in_scope(path):
                        violations.append(path)

                if violations:
                    logger.error(
                        "Out-of-scope changes for %s (after cleanup): %s",
                        task_id,
                        ", ".join(sorted(set(violations))),
                    )
                    return False

        # Stage only in-scope paths (plus any already-staged housekeeping changes).
        if allowed_paths:
            to_add: list[str] = []
            for raw in allowed_paths:
                path = (raw or "").strip()
                if not path:
                    continue
                probe = workdir / path.rstrip("/")
                if probe.exists():
                    to_add.append(path)
                else:
                    logger.warning(
                        "Skipping non-existent allowed_path for %s: %s",
                        task_id,
                        path,
                    )
            # Also stage per-task UAT artifacts if they were generated.
            for rel in sorted(uat_artifacts):
                if (workdir / rel).exists():
                    to_add.append(rel)
            add_cmd = ["git", "add", "--", *(to_add or allowed_paths)]
        else:
            logger.warning("Task %s has no allowed_paths; staging all changes", task_id)
            add_cmd = ["git", "add", "."]

        result = await manager.run(add_cmd, cwd=workdir)
        if not result["success"]:
            logger.error("Failed to stage changes: %s", result["output"])
            return False

        staged = await manager.run(["git", "diff", "--cached", "--name-only"], cwd=workdir)
        if staged["success"] and not staged["output"].strip():
            logger.info("No changes to commit; skipping commit")
            return True

        commit_message = self._generate_commit_message(task_id, task)
        result = await manager.run(
            ["git", "commit", "--no-verify", "-m", commit_message],
            cwd=workdir,
        )
        if not result["success"]:
            logger.error("Failed to commit: %s", result["output"])
            return False

        logger.info("Committed changes in: %s", workdir)
        return True

    async def _push_step(self, task_id: str, task, iterations: int) -> None:
        """Push changes and create PR.

        Args:
            task_id: Task ID
            task: ParsedTask
            iterations: Number of iterations used
        """
        logger.info(f"Push step for {task_id}")

        branch_name = f"autopilot/{task_id}"

        # Push branch
        result = await self.github.push_branch(branch_name)

        if not result["success"]:
            logger.error(f"Push failed: {result.get('error', '')}")
            return

        # Create PR if enabled
        if self.config.github.create_pr:
            # Calculate metrics
            try:
                # Get changed files count
                changed_files = await self.git_ops.list_files_changed()
                files_changed = len(changed_files)

                # Get lines changed from diff
                diff_output = await self.git_ops.get_diff()
                lines_changed = len(diff_output.splitlines())
            except Exception as e:
                logger.warning(f"Failed to calculate PR metrics: {e}")
                files_changed = 0
                lines_changed = 0

            pr_description = self.github.generate_pr_description(
                task_id=task_id,
                task_title=task.title,
                task_goal=task.goal,
                acceptance_criteria=task.acceptance_criteria,
                iterations=iterations,
                files_changed=files_changed,
                lines_changed=lines_changed,
            )

            pr_result = await self.github.create_pr(
                branch_name=branch_name,
                title=f"[autopilot] {task_id}: {task.title}",
                description=pr_description,
            )

            if pr_result.success:
                logger.info(f"PR created: {pr_result.pr_url}")
            else:
                logger.error(f"PR creation failed: {pr_result.error_message}")

    def _generate_commit_message(self, task_id: str, task) -> str:
        """Generate commit message.

        Args:
            task_id: Task ID
            task: ParsedTask

        Returns:
            Commit message
        """
        lines = [
            f"[autopilot] {task_id}: {task.title}",
            "",
            f"{task.goal}",
            "",
            "Co-Authored-By: Autopilot <noreply@autopilot>",
        ]

        return "\n".join(lines)

    async def resume(self, task_path: Path | None = None) -> bool:
        """Resume interrupted execution.

        Args:
            task_path: Optional path to task file to resume

        Returns:
            True if resume succeeded
        """
        logger.info("Resuming execution")

        # Check current state
        if self.machine.current_state == OrchestratorState.DONE:
            logger.info("Already complete")
            return True

        if self.machine.current_state == OrchestratorState.FAILED:
            logger.error("Cannot resume from FAILED state")
            return False

        current_task_id = self.machine.state.current_task_id

        # If task_path not provided, try to find it automatically
        if not task_path and current_task_id:
            task_path = await self._find_task_file(current_task_id)

        # If we have a task path, re-execute it
        if task_path:
            logger.info(f"Resuming task: {task_path}")
            return await self.run_single_task(task_path)

        # If no task found or no current task
        if current_task_id:
            logger.error(f"Could not find task file for: {current_task_id}")
            logger.error("Please specify task file explicitly:")
            logger.error(f"  autopilot resume tasks/{current_task_id}.md")
        else:
            logger.warning("No task to resume")

        return False

    async def _find_task_file(self, task_id: str) -> Path | None:
        """Find task file by task ID.

        Args:
            task_id: Task ID to find

        Returns:
            Path to task file or None
        """
        from pathlib import Path

        # Check .autopilot/plan/tasks/ first (generated from plans)
        plan_tasks_dir = Path(".autopilot/plan/tasks")
        if plan_tasks_dir.exists():
            task_file = plan_tasks_dir / f"{task_id}.md"
            if task_file.exists():
                logger.info(f"Found task file from plan: {task_file}")
                return task_file

        # Check tasks/ directory (user-created tasks)
        tasks_dir = Path("tasks")
        if tasks_dir.exists():
            task_file = tasks_dir / f"{task_id}.md"
            if task_file.exists():
                logger.info(f"Found task file from tasks/: {task_file}")
                return task_file

        logger.warning(f"Could not find task file for: {task_id}")
        return None
