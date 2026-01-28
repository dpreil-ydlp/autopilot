"""Plan expander using Codex agent."""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..agents.codex import CodexAgent
from .parser import ParsedTask, parse_task_file

logger = logging.getLogger(__name__)


@dataclass
class TaskDAG:
    """Task dependency graph."""

    tasks: dict[str, ParsedTask]
    edges: list[tuple[str, str]]  # (from, to) dependencies
    topo_order: list[str]  # Topological sort
    parallel_batches: list[list[str]]  # Parallelizable groups


class PlanExpanderError(Exception):
    """Plan expansion error."""

    pass


async def expand_plan(
    plan_path: Path,
    planner_config: dict,
    output_dir: Optional[Path] = None,
) -> TaskDAG:
    """Expand plan file into task DAG.

    Args:
        plan_path: Path to plan markdown file
        planner_config: Planner agent configuration
        output_dir: Directory to write task files (default: .autopilot/plan/tasks/)

    Returns:
        TaskDAG with parsed tasks and dependency graph

    Raises:
        PlanExpanderError: If plan expansion fails
    """
    if not plan_path.exists():
        raise PlanExpanderError(f"Plan file not found: {plan_path}")

    with open(plan_path, "r") as f:
        plan_content = f.read()

    # Invoke Codex agent for planning
    agent = CodexAgent(planner_config)

    try:
        logger.info(f"Expanding plan: {plan_path}")
        plan_result = await agent.plan(
            plan_content=plan_content,
            timeout_sec=planner_config.get("timeout_sec", 300),
            work_dir=plan_path.parent,
        )

        # Extract DAG structure from plan result
        tasks_data = plan_result.get("tasks", [])
        edges = plan_result.get("edges", [])
        topo_order = plan_result.get("topo_order", [])
        parallel_batches = plan_result.get("parallel_batches", [])

        # Set output directory
        if output_dir is None:
            output_dir = Path(".autopilot/plan/tasks")
        output_dir.mkdir(parents=True, exist_ok=True)

        # Parse and materialize tasks
        tasks = {}
        for task_data in tasks_data:
            if not isinstance(task_data, dict):
                raise PlanExpanderError(f"Invalid task entry in plan output: {task_data}")
            task_id = task_data.get("id")
            if not task_id:
                task_id = f"task-{len(tasks) + 1}"
                logger.warning(f"Plan task missing id; assigning {task_id}")
            title = task_data["title"]
            description = task_data.get("description", "")
            dependencies = task_data.get("depends_on") or task_data.get("dependencies", [])
            validation_commands = task_data.get("validation_commands", {})

            # Extract enriched fields with defaults
            goal = task_data.get("goal", description)
            acceptance_criteria = task_data.get("acceptance_criteria", [])
            allowed_paths = task_data.get("allowed_paths", ["src/", "tests/"])
            skills_used = (
                task_data.get("suggested_claude_skills")
                or task_data.get("suggested_skills")
                or task_data.get("skills_used", [])
            )
            mcp_servers_used = (
                task_data.get("suggested_mcp_servers")
                or task_data.get("mcp_servers_used", [])
            )
            subagents_used = (
                task_data.get("suggested_subagents")
                or task_data.get("subagents_used", [])
            )
            estimated_complexity = task_data.get("estimated_complexity", "medium")

            # Create task file content with enriched metadata
            task_content = _generate_task_file(
                task_id=task_id,
                title=title,
                description=description,
                dependencies=dependencies,
                goal=goal,
                acceptance_criteria=acceptance_criteria,
                allowed_paths=allowed_paths,
                skills_used=skills_used,
                mcp_servers_used=mcp_servers_used,
                subagents_used=subagents_used,
                estimated_complexity=estimated_complexity,
                validation_commands=validation_commands,
            )

            # Write task file
            task_path = output_dir / f"{task_id}.md"
            with open(task_path, "w") as f:
                f.write(task_content)

            # Parse the generated task file
            parsed_task = parse_task_file(task_path)
            tasks[task_id] = parsed_task

            logger.info(f"Materialized task: {task_id} - {title} ({estimated_complexity} complexity)")

        # Write DAG artifact
        dag_artifact = Path(".autopilot/plan/dag.json")
        dag_artifact.parent.mkdir(parents=True, exist_ok=True)
        with open(dag_artifact, "w") as f:
            json.dump(
                {
                    "tasks": list(tasks.keys()),
                    "edges": edges,
                    "topo_order": topo_order,
                    "parallel_batches": parallel_batches,
                },
                f,
                indent=2,
            )

        logger.info(f"Plan expanded to {len(tasks)} tasks")

        return TaskDAG(
            tasks=tasks,
            edges=[tuple(edge) for edge in edges],
            topo_order=topo_order,
            parallel_batches=parallel_batches,
        )

    except Exception as e:
        raise PlanExpanderError(f"Plan expansion failed: {e}") from e


def _generate_task_file(
    task_id: str,
    title: str,
    description: str,
    dependencies: list[str],
    goal: str = "",
    acceptance_criteria: list[str] = None,
    allowed_paths: list[str] = None,
    skills_used: list[str] = None,
    mcp_servers_used: list[str] = None,
    subagents_used: list[str] = None,
    estimated_complexity: str = "medium",
    validation_commands: dict[str, str] = None,
) -> str:
    """Generate task file content with enriched metadata.

    Args:
        task_id: Task identifier
        title: Task title
        description: Task description
        dependencies: List of task IDs this task depends on
        goal: Specific goal for this task
        acceptance_criteria: List of acceptance criteria
        allowed_paths: List of allowed path patterns
        skills_used: List of Claude Code skills to use
        mcp_servers_used: List of MCP servers to use
        subagents_used: List of subagents to invoke
        estimated_complexity: Complexity estimate
        validation_commands: Optional validation command overrides

    Returns:
        Task file markdown content
    """
    if acceptance_criteria is None:
        acceptance_criteria = []
    if allowed_paths is None:
        allowed_paths = ["src/", "tests/"]
    if skills_used is None:
        skills_used = []
    if mcp_servers_used is None:
        mcp_servers_used = []
    if subagents_used is None:
        subagents_used = []
    if validation_commands is None:
        validation_commands = {}

    lines = [
        f"# Task: {title}",
        "",
        f"**Task ID:** {task_id}",
        f"**Estimated Complexity:** {estimated_complexity}",
        "",
    ]

    # Goal section
    lines.extend([
        "## Goal",
        goal or description,
        "",
    ])

    # Dependencies section
    if dependencies:
        lines.extend([
            "## Dependencies",
            f"This task depends on: {', '.join(dependencies)}",
            "",
        ])

    # Acceptance criteria section (always present)
    lines.extend([
        "## Acceptance Criteria",
    ])
    if acceptance_criteria:
        for i, criterion in enumerate(acceptance_criteria, 1):
            lines.append(f"- [ ] {criterion}")
    else:
        lines.extend([
            "- [ ] Task completed according to description",
            "- [ ] Code follows project conventions",
            "- [ ] Tests pass",
        ])
    lines.append("")

    # Constraints section
    lines.extend([
        "## Constraints",
        "- Follow existing code patterns",
        "- Maintain backward compatibility",
        "",
    ])

    # Allowed paths section
    lines.extend([
        "## Allowed Paths",
    ])
    for path in allowed_paths:
        lines.append(f"- {path}")
    lines.append("")

    # Enriched metadata section
    if skills_used or mcp_servers_used or subagents_used:
        lines.extend([
            "## Agent Guidance",
        ])

        if skills_used:
            lines.append(f"**Recommended Skills:** {', '.join(skills_used)}")
        if mcp_servers_used:
            lines.append(f"**MCP Servers:** {', '.join(mcp_servers_used)}")
        if subagents_used:
            lines.append(f"**Subagents:** {', '.join(subagents_used)}")

        lines.append("")

    # Validation commands section
    if validation_commands:
        commands = {k: v for k, v in validation_commands.items() if v}
    else:
        commands = {
            "tests": "pytest -q",
            "lint": "ruff check .",
            "format": "ruff format .",
            "uat": "pytest -q tests/uat",
        }

    lines.extend(["## Validation Commands", "```yaml"])
    for key, value in commands.items():
        lines.append(f"{key}: {value}")
    lines.extend(["```", ""])

    # UAT section
    lines.extend([
        "## User Acceptance Tests",
        "1. Verify the feature works as described",
        "2. Check edge cases",
        "3. Ensure proper error handling",
        "",
    ])

    # Notes section
    notes = ["This task was auto-generated from a plan."]
    if estimated_complexity == "high" or estimated_complexity == "critical":
        notes.append(f"WARNING: This task is marked as {estimated_complexity.upper()} complexity - allow extra time and review.")

    lines.extend([
        "## Notes",
    ])
    for note in notes:
        lines.append(f"- {note}")
    lines.append("")

    return "\n".join(lines)


def compute_ready_set(dag: TaskDAG, completed_tasks: set[str]) -> set[str]:
    """Compute set of tasks ready to execute.

    A task is ready if all its dependencies are completed.

    Args:
        dag: Task dependency graph
        completed_tasks: Set of completed task IDs

    Returns:
        Set of task IDs that are ready to execute
    """
    ready = set()

    for task_id in dag.topo_order:
        if task_id in completed_tasks:
            continue

        # Get dependencies for this task
        deps = {dep for (dep, to) in dag.edges if to == task_id}

        # Check if all dependencies are completed
        if deps.issubset(completed_tasks):
            ready.add(task_id)

    return ready


def validate_dag(dag: TaskDAG) -> list[str]:
    """Validate DAG structure and return list of errors.

    Args:
        dag: Task dependency graph

    Returns:
        List of error messages (empty if valid)
    """
    errors = []

    # Check that all tasks exist in topo order
    topo_set = set(dag.topo_order)
    for task_id in dag.tasks:
        if task_id not in topo_set:
            errors.append(f"Task {task_id} not in topological order")

    # Check that all edges reference valid tasks
    for from_id, to_id in dag.edges:
        if from_id not in dag.tasks:
            errors.append(f"Edge references non-existent task: {from_id}")
        if to_id not in dag.tasks:
            errors.append(f"Edge references non-existent task: {to_id}")

    # Check for cycles (simple check: if topo_order doesn't contain all tasks)
    if len(dag.topo_order) != len(dag.tasks):
        errors.append("Possible cycle in task dependencies")

    # Check that parallel batches are valid
    all_batched = set()
    for batch in dag.parallel_batches:
        for task_id in batch:
            if task_id not in dag.tasks:
                errors.append(f"Parallel batch references non-existent task: {task_id}")
            if task_id in all_batched:
                errors.append(f"Task {task_id} appears in multiple parallel batches")
            all_batched.add(task_id)

    return errors
