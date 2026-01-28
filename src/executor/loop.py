"""Main execution loop orchestrating all components."""

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..agents.base import AgentError
from ..agents.claude import ClaudeAgent
from ..agents.codex import CodexAgent
from ..config.models import AutopilotConfig
from ..integrations.github import GitHubIntegration
from ..observability.dashboard import StatusDashboard, TerminalDashboard
from ..safety.guards import SafetyChecker, SafetyError
from ..scheduler.dag import DAGScheduler
from ..state.machine import OrchestratorMachine
from ..state.persistence import (
    OrchestratorState,
    TaskState,
    WorkerState,
    LastBuildState,
    LastValidateState,
    LastReviewState,
)
from ..tasks.parser import parse_task_file, validate_task_constraints
from ..tasks.plan import TaskDAG, expand_plan
from ..utils.git import GitOps
from ..utils.subprocess import SubprocessManager
from ..validation.runner import ValidationRunner

logger = logging.getLogger(__name__)


class ExecutionLoop:
    """Main execution loop for Autopilot."""

    def __init__(
        self,
        config: AutopilotConfig,
        state_path: Optional[Path] = None,
        verbose: bool = False,
    ):
        """Initialize execution loop.

        Args:
            config: Autopilot configuration
            state_path: Path to state file
            verbose: Enable verbose output
        """
        self.config = config
        self.verbose = verbose
        self.max_workers = 1  # Default, can be overridden per execution

        # State management
        state_path = state_path or Path(".autopilot/state.json")
        self.machine = OrchestratorMachine(state_path)
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
        self.builder = ClaudeAgent(config.builder.model_dump())
        self.reviewer = CodexAgent(config.reviewer.model_dump())
        self.planner = CodexAgent(config.planner.model_dump())

        # GitHub integration (if enabled)
        self.github = None
        if config.github.enabled:
            self.github = GitHubIntegration(
                git_ops=self.git_ops,
                remote=config.github.remote_name,
                base_branch=config.github.base_branch,
            )

    async def run_single_task(self, task_path: Path) -> bool:
        """Run a single task file.

        Args:
            task_path: Path to task file

        Returns:
            True if task succeeded
        """
        logger.info(f"Running single task: {task_path}")

        try:
            # Parse task
            task = parse_task_file(task_path)
            violations = validate_task_constraints(task)

            if violations:
                logger.error(f"Task validation failed: {violations}")
                return False

            # Check safety
            await self.safety.check_before_transition()

            # Create DAG with single task
            dag = TaskDAG(
                tasks={task.task_id: task},
                edges=[],
                topo_order=[task.task_id],
                parallel_batches=[[task.task_id]],
            )

            # Execute task
            return await self._execute_dag(dag, max_workers=1)  # Single task = 1 worker

        except Exception as e:
            logger.error(f"Task execution failed: {e}")
            return False

    async def run_plan(self, plan_path: Path, max_workers: int = 1) -> bool:
        """Run a plan file.

        Args:
            plan_path: Path to plan file
            max_workers: Maximum parallel workers

        Returns:
            True if plan succeeded
        """
        logger.info(f"Running plan: {plan_path}")

        try:
            # Expand plan into DAG
            dag = await expand_plan(
                plan_path=plan_path,
                planner_config=self.config.planner.model_dump(),
            )

            # Validate DAG
            from ..tasks.plan import validate_dag
            errors = validate_dag(dag)

            if errors:
                logger.error(f"DAG validation failed: {errors}")
                return False

            # Execute DAG
            return await self._execute_dag(dag, max_workers=max_workers)

        except Exception as e:
            logger.error(f"Plan execution failed: {e}")
            return False

    async def _execute_dag(self, dag: TaskDAG, max_workers: Optional[int] = None) -> bool:
        """Execute task DAG.

        Args:
            dag: Task dependency graph
            max_workers: Maximum parallel workers (overrides default)

        Returns:
            True if all tasks succeeded
        """
        # Use provided max_workers or default
        workers = max_workers or self.max_workers

        # Create scheduler with git_ops for worktree support
        scheduler = DAGScheduler(
            dag=dag,
            max_workers=workers,
            work_dir=self.config.repo.root,
            git_ops=self.git_ops,
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
                    task = dag.tasks[task_id]
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

            # Execute all dispatched tasks concurrently
            async def execute_on_worker(task_id: str, worker_id: str, worker):
                """Execute task on assigned worker."""
                try:
                    task = dag.tasks[task_id]
                    scheduler.mark_task_running(task_id, worker_id=worker_id)
                    success = await worker.execute_task(task, self, scheduler)
                    return task_id, worker_id, success
                except Exception as e:
                    logger.error(f"Task {task_id} raised exception: {e}")
                    return task_id, worker_id, False

            # Run all dispatched tasks concurrently
            results = await asyncio.gather(
                *[execute_on_worker(tid, wid, w) for tid, wid, w in dispatched],
                return_exceptions=True
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
            self.dashboard.update()
            self.terminal.print_state(self.machine.state)

        # Final UAT for plan-level execution (if configured)
        if not scheduler.has_failures():
            final_uat_ok = await self._final_uat_step(dag)
            if not final_uat_ok:
                return False

        # Check final status
        return not scheduler.has_failures()

    async def _execute_task(
        self,
        task,
        scheduler: DAGScheduler,
        workdir: Optional[Path] = None,
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
        for iteration in range(max_iterations):
            iterations_used = iteration + 1
            logger.info(f"Iteration {iterations_used}/{max_iterations}")

            # Build
            build_success = await self._build_step(
                task_id,
                task,
                workdir=workdir,
                build_context=last_validation_summary,
            )
            if not build_success:
                return False

            # Validate
            validate_success, validation_results, validation_summary = await self._validate_step(
                task_id, task, workdir=workdir
            )
            if not validate_success:
                last_validation_summary = validation_summary
                # Feed into FIX loop
                continue
            last_validation_summary = ""

            # Review
            review_success = await self._review_step(task_id, task, validation_results, workdir=workdir)
            if not review_success:
                # Feed into FIX loop
                continue

            # Generate UAT cases (if configured)
            uat_gen_success = await self._generate_uat_step(task_id, task, workdir=workdir)
            if not uat_gen_success:
                logger.warning("UAT generation failed, continuing with existing UAT")

            # UAT
            uat_success = await self._uat_step(task_id, task, workdir=workdir)
            if not uat_success:
                # Feed into FIX loop
                continue

            # Task complete
            break

        # Commit changes (in worktree if applicable)
        await self._commit_step(task_id, task, workdir=workdir)

        # If using worktree, merge branch back to main
        if workdir and workdir != self.config.repo.root:
            logger.info(f"Merging worktree changes back to main branch")
            try:
                await self._merge_worktree_to_main(workdir, branch_name)
            except Exception as e:
                logger.error(f"Failed to merge worktree changes: {e}")
                return False

        # Push if GitHub enabled (only from main repo)
        if self.github and workdir == self.config.repo.root:
            await self._push_step(task_id, task, iterations_used)

        return True

    async def _merge_worktree_to_main(self, worktree_path: Path, branch_name: str) -> None:
        """Merge worktree branch back to main branch.

        Args:
            worktree_path: Path to worktree
            branch_name: Branch name to merge
        """
        manager = SubprocessManager(timeout_sec=60)

        # Switch back to default branch in worktree
        default_branch = self.config.repo.default_branch
        result = await manager.run(["git", "checkout", default_branch], cwd=worktree_path)
        if not result["success"]:
            raise Exception(f"Failed to checkout default branch: {result['output']}")

        # Merge the task branch
        result = await manager.run(
            ["git", "merge", "--no-ff", branch_name],
            cwd=worktree_path,
        )
        if not result["success"]:
            raise Exception(f"Failed to merge branch: {result['output']}")

        logger.info(f"Merged {branch_name} into {default_branch}")

    async def _build_step(
        self,
        task_id: str,
        task,
        workdir: Optional[Path] = None,
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
            prompt = task.raw_content
            if build_context:
                prompt = f"{prompt}\n\n## Validation Feedback\n{build_context}\n"
            result = await self.builder.execute(
                prompt=prompt,
                timeout_sec=self.config.loop.build_timeout_sec,
                work_dir=workdir,
            )

            if result["success"]:
                logger.info(f"Build succeeded")
                return True
            else:
                logger.error(f"Build failed: {result.get('output', '')}")
                return False

        except AgentError as e:
            logger.error(f"Build error: {e}")
            return False

    async def _validate_step(
        self, task_id: str, task, workdir: Optional[Path] = None
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
        )

        try:
            results = await runner.run_all(
                commands=task.validation_commands,
            )

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

    async def _review_step(self, task_id: str, task, validation_results: dict, workdir: Optional[Path] = None) -> bool:
        """Execute review step.

        Args:
            task_id: Task ID
            task: ParsedTask
            validation_results: Results from validation step
            workdir: Working directory (uses repo root if not specified)

        Returns:
            True if review passed
        """
        logger.info(f"Review step for {task_id}")

        workdir = workdir or self.config.repo.root

        try:
            # Get diff from worktree
            if workdir != self.config.repo.root:
                # Get diff from worktree
                manager = SubprocessManager(timeout_sec=30)
                result = await manager.run(["git", "diff"], cwd=workdir)
                diff = result["output"]
            else:
                diff = await self.git_ops.get_diff()

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
                return True

            # Skip review if emergency mode
            if self.safety.should_skip_review():
                logger.warning("⚠️  Skipping review (EMERGENCY MODE)")
                return True

            # Execute reviewer
            result = await self.reviewer.review(
                diff=diff,
                validation_output=validation_output,
                timeout_sec=self.config.loop.review_timeout_sec,
                work_dir=workdir,
            )

            if result["verdict"] == "approve":
                logger.info(f"Review approved")
                return True
            else:
                logger.warning(f"Review requested changes: {result.get('feedback', '')}")
                return False

        except AgentError as e:
            logger.error(f"Review error: {e}")
            return False

    async def _generate_uat_step(self, task_id: str, task, workdir: Optional[Path] = None) -> bool:
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

        # Check if UAT command exists
        uat_command = task.validation_commands.get("uat")
        if not uat_command:
            logger.info("No UAT command configured, skipping UAT generation")
            return True

        try:
            # Get current diff from worktree
            if workdir != self.config.repo.root:
                manager = SubprocessManager(timeout_sec=30)
                result = await manager.run(["git", "diff"], cwd=workdir)
                diff = result["output"]
            else:
                diff = await self.git_ops.get_diff()

            # Generate UAT Python code using Codex
            uat_content = await self.planner.generate_uat(
                task_content=task.raw_content,
                diff=diff,
                timeout_sec=self.config.loop.uat_generate_timeout_sec,
                work_dir=workdir,
            )

            # Normalize content to executable Python
            uat_code = self._normalize_uat_code(uat_content)

            # Write UAT artifacts
            from pathlib import Path
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
                uat_code = "\n".join([
                    "import pytest",
                    "",
                    "pytest.skip(",
                    f"    \"UAT cases generated at {uat_md} (no executable tests produced)\",",
                    "    allow_module_level=True,",
                    ")",
                ])

            with open(uat_file, "w") as f:
                f.write(uat_code)

            logger.info(f"UAT generated: {uat_file} (raw: {uat_md})")
            return True

        except AgentError as e:
            logger.error(f"UAT generation error: {e}")
            return False

    async def _uat_step(self, task_id: str, task, workdir: Optional[Path] = None) -> bool:
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
        )

        try:
            uat_command = task.validation_commands.get("uat")
            if not uat_command:
                logger.info("No UAT command configured, skipping")
                return True

            result = await runner.run_uat(uat_command)

            if result.success:
                logger.info(f"UAT passed")
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

        # Generate final UAT cases from combined tasks + diff
        try:
            combined_tasks = "\n\n".join([t.raw_content for t in dag.tasks.values()])
            diff = await self.git_ops.get_diff()
            uat_content = await self.planner.generate_uat(
                task_content=combined_tasks,
                diff=diff,
                timeout_sec=self.config.orchestrator.final_uat_timeout_sec,
                work_dir=self.config.repo.root,
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
                uat_code = "\n".join([
                    "import pytest",
                    "",
                    "pytest.skip(",
                    "    \"Final UAT generated as markdown (no executable tests produced)\",",
                    "    allow_module_level=True,",
                    ")",
                ])
            with open(uat_py, "w") as f:
                f.write(uat_code)
        except Exception as e:
            logger.warning(f"Final UAT generation failed: {e}")

        # Run UAT command
        runner = ValidationRunner(
            work_dir=self.config.repo.root,
            timeout_sec=self.config.orchestrator.final_uat_timeout_sec,
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

        code_block = text[fence_start + 3:fence_end]
        stripped = code_block.lstrip()
        if stripped.startswith("python"):
            stripped = stripped[len("python"):]
        return stripped.strip()

    async def _commit_step(self, task_id: str, task, workdir: Optional[Path] = None) -> None:
        """Commit changes.

        Args:
            task_id: Task ID
            task: ParsedTask
            workdir: Working directory (uses repo root if not specified)
        """
        logger.info(f"Commit step for {task_id}")

        workdir = workdir or self.config.repo.root

        # Stage and commit changes in worktree
        if workdir != self.config.repo.root:
            # Commit in worktree
            manager = SubprocessManager(timeout_sec=30)

            # Stage all changes
            result = await manager.run(["git", "add", "."], cwd=workdir)
            if not result["success"]:
                logger.error(f"Failed to stage changes in worktree: {result['output']}")
                return

            # Commit
            commit_message = self._generate_commit_message(task_id, task)
            result = await manager.run(
                ["git", "commit", "-m", commit_message],
                cwd=workdir,
            )
            if not result["success"]:
                logger.error(f"Failed to commit in worktree: {result['output']}")
                return

            logger.info(f"Committed changes in worktree: {workdir}")
        else:
            # Stage all changes
            changed_files = await self.git_ops.list_files_changed()
            await self.git_ops.add(changed_files)

            # Commit
            commit_message = self._generate_commit_message(task_id, task)
            await self.git_ops.commit(commit_message)

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
            f"Co-Authored-By: Autopilot <noreply@autopilot>",
        ]

        return "\n".join(lines)

    async def resume(self, task_path: Optional[Path] = None) -> bool:
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

    async def _find_task_file(self, task_id: str) -> Optional[Path]:
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
