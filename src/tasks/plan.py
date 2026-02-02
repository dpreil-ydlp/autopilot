"""Plan expander using Codex or Claude agent."""

import json
import logging
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..agents.codex import CodexAgent
from ..agents.claude import ClaudeAgent
from .chunker import PlanChunker
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
    output_dir: Path | None = None,
    progress_callback: Callable[[str], None] | None = None,
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

    # Check if plan needs chunking due to size
    chunker = PlanChunker(plan_path)
    metadata = chunker.analyze()

    if metadata["needs_chunking"]:
        logger.info(
            f"Large plan detected ({metadata['total_tokens']:.0f} tokens). "
            f"Using chunked expansion."
        )
        return await _expand_chunked_plan(
            plan_path,
            planner_config,
            metadata,
            chunker,
            output_dir,
            progress_callback,
        )

    with open(plan_path) as f:
        plan_content = f.read()

    # Invoke agent for planning (Codex or Claude based on config)
    planner_mode = (planner_config.get("mode") or "codex_cli").lower()
    if planner_mode == "claude":
        agent = ClaudeAgent(planner_config)
        agent_name = "Claude"
    else:
        agent = CodexAgent(planner_config)
        agent_name = "Codex"

    try:
        if progress_callback:
            progress_callback(f"{agent_name} planning started")
        logger.info(f"Expanding plan: {plan_path} using {agent_name}")
        plan_context = f"Plan file: {plan_path.name}\nPlan size: {len(plan_content)} characters"
        # Don't pass work_dir to prevent Claude from accessing repo files during planning
        plan_result = await agent.plan(
            plan_content=plan_content,
            timeout_sec=planner_config.get("timeout_sec", 300),
            work_dir=None,  # Run in isolated context, not in repo directory
            context=plan_context,
        )
        if progress_callback:
            progress_callback(f"{agent_name} planning completed")

        # Extract DAG structure from plan result
        tasks_data = plan_result.get("tasks", [])
        raw_edges = plan_result.get("edges", [])

        # Persist raw output for debugging.
        raw_artifact = Path(".autopilot/plan/dag_raw.json")
        raw_artifact.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(raw_artifact, "w") as f:
                json.dump(plan_result, f, indent=2)
        except Exception:
            pass

        # Set output directory
        if output_dir is None:
            output_dir = Path(".autopilot/plan/tasks")
        output_dir.mkdir(parents=True, exist_ok=True)

        # Parse and materialize tasks
        tasks: dict[str, ParsedTask] = {}
        assigned_ids: list[str] = []
        for task_data in tasks_data:
            if not isinstance(task_data, dict):
                raise PlanExpanderError(f"Invalid task entry in plan output: {task_data}")
            task_id = task_data.get("id")
            if not task_id:
                task_id = f"task-{len(tasks) + 1}"
                logger.warning(f"Plan task missing id; assigning {task_id}")
                task_data["id"] = task_id
            assigned_ids.append(task_id)
            description = task_data.get("description") or task_data.get("goal") or ""
            title = task_data.get("title") or (description if description else f"Task {task_id}")
            dependencies = task_data.get("depends_on") or task_data.get("dependencies", [])
            validation_commands = task_data.get("validation_commands", {})
            if not isinstance(validation_commands, dict):
                logger.warning(
                    "Plan task %s has non-dict validation_commands; ignoring",
                    task_id,
                )
                validation_commands = {}

            # Normalize dependencies list
            if isinstance(dependencies, str):
                dependencies = [dependencies]
            if not isinstance(dependencies, list):
                dependencies = []
            dependencies = [str(dep).strip() for dep in dependencies if str(dep).strip()]

            # Extract enriched fields with defaults
            goal = task_data.get("goal", description)
            acceptance_criteria = task_data.get("acceptance_criteria", [])
            raw_allowed_paths = task_data.get("allowed_paths")
            inferred_allowed = _infer_allowed_paths(title, description, plan_path.parent)

            def is_generic_allowed_paths(paths: list[str]) -> bool:
                normalized = {p.rstrip("/").strip() for p in paths if p and str(p).strip()}
                return bool(normalized) and normalized.issubset({"src", "tests"})

            if isinstance(raw_allowed_paths, list) and raw_allowed_paths:
                allowed_paths = [str(p) for p in raw_allowed_paths if str(p).strip()]
                # Prefer deterministic inference when the planner provided only generic defaults
                # (src/tests) but we can clearly infer a more specific top-level folder.
                if inferred_allowed and is_generic_allowed_paths(allowed_paths):
                    allowed_paths = inferred_allowed
            else:
                allowed_paths = inferred_allowed or [
                    "src/",
                    "tests/",
                ]
            skills_used = (
                task_data.get("suggested_claude_skills")
                or task_data.get("suggested_skills")
                or task_data.get("skills_used", [])
            )
            mcp_servers_used = task_data.get("suggested_mcp_servers") or task_data.get(
                "mcp_servers_used", []
            )
            subagents_used = task_data.get("suggested_subagents") or task_data.get(
                "subagents_used", []
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

            logger.info(
                f"Materialized task: {task_id} - {title} ({estimated_complexity} complexity)"
            )
            if progress_callback:
                progress_callback(f"Task created: {task_id} - {title}")

        task_id_list = list(tasks.keys())
        task_id_set = set(task_id_list)

        edges = _merge_edges(raw_edges, tasks_data, task_id_set)
        topo_order, parallel_batches, cycle_nodes = _toposort_batches(task_id_list, edges)
        if cycle_nodes:
            raise PlanExpanderError(
                "Cycle detected in planned task dependencies: "
                + ", ".join(sorted(cycle_nodes)[:20])
                + (" ..." if len(cycle_nodes) > 20 else "")
            )

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


async def _expand_chunked_plan(
    plan_path: Path,
    planner_config: dict,
    metadata: dict,
    chunker: PlanChunker,
    output_dir: Path | None,
    progress_callback: Callable[[str], None] | None,
) -> TaskDAG:
    """Expand a large plan by processing it in chunks.

    Args:
        plan_path: Path to plan markdown file
        planner_config: Planner agent configuration
        metadata: Plan analysis metadata from chunker
        chunker: PlanChunker instance
        output_dir: Directory to write task files
        progress_callback: Optional progress callback

    Returns:
        TaskDAG with all tasks from all chunks

    Raises:
        PlanExpanderError: If chunked expansion fails
    """
    # Create chunks
    chunks = chunker.create_chunks(metadata)
    logger.info(f"Expanding plan in {len(chunks)} chunks")

    # Set output directory
    if output_dir is None:
        output_dir = Path(".autopilot/plan/tasks")
    output_dir.mkdir(parents=True, exist_ok=True)

    # Create temp directory for chunk files
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # Determine agent to use
        planner_mode = (planner_config.get("mode") or "codex_cli").lower()
        if planner_mode == "claude":
            agent = ClaudeAgent(planner_config)
            agent_name = "Claude"
        else:
            agent = CodexAgent(planner_config)
            agent_name = "Codex"

        # Process each chunk
        all_tasks = {}
        global_edges = []
        task_id_offset = 0
        chunk_task_counts = []

        for i, chunk in enumerate(chunks):
            chunk_num = i + 1
            logger.info(
                f"Processing chunk {chunk_num}/{len(chunks)}: {chunk.id} "
                f"({chunk.estimated_tokens:.0f} tokens, {len(chunk.sections)} sections)"
            )

            if progress_callback:
                progress_callback(
                    f"Processing chunk {chunk_num}/{len(chunks)}: {chunk.id}"
                )

            # Write chunk to temp file
            chunk_path = temp_path / f"chunk_{chunk_num}.md"
            chunker.write_chunk(chunk, chunk_path)

            # Read chunk content
            with open(chunk_path) as f:
                chunk_content = f.read()

            # Detect meta-content chunks (testing, documentation, etc.)
            # Use more specific patterns to avoid false positives on implementation content
            meta_content_patterns = [
                "## performance testing", "## security testing", "## documentation",
                "### supporting documents", "source files:", "## implementation summary",
                "## verification plan", "## testing checklist", "## known issues",
                "## summary"  # Catch-all for summary sections at end
            ]
            chunk_lower = chunk_content.lower()
            meta_content_score = sum(1 for pattern in meta_content_patterns if pattern in chunk_lower)

            # If chunk is primarily meta-content (2+ heading-level patterns), skip expansion
            # Using 2 instead of 3 since patterns are now more specific (headings with ##)
            if meta_content_score >= 2:
                logger.info(
                    f"Chunk {chunk_num}/{len(chunks)}: Detected meta-content "
                    f"({meta_content_score} patterns), skipping DAG expansion"
                )
                # Write empty chunk result
                chunk_tasks = []
                raw_edges = []

                # Persist empty result for debugging
                chunk_raw = output_dir / f".chunk_{chunk_num}_raw.json"
                try:
                    with open(chunk_raw, "w") as f:
                        json.dump({"tasks": [], "edges": [], "meta_content": True}, f, indent=2)
                except Exception:
                    pass
            else:
                # Expand this chunk (implementation tasks)
                try:
                    chunk_context = f"Chunk {chunk_num}/{len(chunks)}: {chunk.id}\nPlan size: {len(chunk_content)} characters"
                    # Use longer timeout for chunked processing (600s instead of 300s)
                    chunk_timeout = planner_config.get("timeout_sec", 300)
                    chunk_timeout = max(chunk_timeout, 600)  # Ensure at least 10 minutes for chunks
                    # Don't pass work_dir to prevent Claude from accessing repo files during planning
                    chunk_result = await agent.plan(
                        plan_content=chunk_content,
                        timeout_sec=chunk_timeout,
                        work_dir=None,  # Run in isolated context, not in repo directory
                        context=chunk_context,
                    )

                    # Extract tasks from this chunk
                    tasks_data = chunk_result.get("tasks", [])
                    raw_edges = chunk_result.get("edges", [])

                    # Persist chunk raw output for debugging
                    chunk_raw = output_dir / f".chunk_{chunk_num}_raw.json"
                    try:
                        with open(chunk_raw, "w") as f:
                            json.dump(chunk_result, f, indent=2)
                    except Exception:
                        pass

                    # Process tasks with ID offset
                    chunk_tasks = []
                    for task_idx, task_data in enumerate(tasks_data):
                        if not isinstance(task_data, dict):
                            logger.warning(
                                f"Chunk {chunk_num}: Invalid task entry, skipping"
                            )
                            continue

                        # Track the original/local task ID from the planner
                        local_task_id = task_data.get("id")
                        if not local_task_id:
                            # Generate local ID for this chunk (1-indexed)
                            local_task_id = f"task-{task_idx + 1}"
                            task_data["id"] = local_task_id

                        # Extract the numeric part for remapping
                        try:
                            local_num = int(local_task_id.split("-")[1])
                        except (ValueError, IndexError):
                            local_num = task_idx + 1

                        # Generate global task ID based on offset
                        global_task_id = f"task-{task_id_offset + local_num}"

                        # Remap dependencies
                        dependencies = task_data.get("depends_on") or task_data.get("dependencies", [])
                        if isinstance(dependencies, str):
                            dependencies = [dependencies]
                        if isinstance(dependencies, list):
                            # Remap dependency IDs to global IDs
                            remapped_deps = []
                            for dep in dependencies:
                                if isinstance(dep, str) and dep.startswith("task-"):
                                    try:
                                        dep_num = int(dep.split("-")[1])
                                        global_dep = f"task-{task_id_offset + dep_num}"
                                        remapped_deps.append(global_dep)
                                    except (ValueError, IndexError):
                                        # Keep original if parsing fails
                                        remapped_deps.append(dep)
                                else:
                                    remapped_deps.append(dep)
                            dependencies = remapped_deps
    
                        # Update task data with remapped dependencies
                        task_data["depends_on"] = dependencies
    
                        # Extract other fields
                        description = task_data.get("description") or task_data.get("goal") or ""
                        title = task_data.get("title") or (description if description else f"Task {global_task_id}")
    
                        # Extract validation commands
                        validation_commands = task_data.get("validation_commands", {})
                        if not isinstance(validation_commands, dict):
                            validation_commands = {}
    
                        # Extract allowed paths
                        raw_allowed_paths = task_data.get("allowed_paths")
                        inferred_allowed = _infer_allowed_paths(title, description, plan_path.parent)
    
                        def is_generic_allowed_paths(paths: list[str]) -> bool:
                            normalized = {
                                p.rstrip("/").strip() for p in paths if p and str(p).strip()
                            }
                            return bool(normalized) and normalized.issubset({"src", "tests"})
    
                        if isinstance(raw_allowed_paths, list) and raw_allowed_paths:
                            allowed_paths = [
                                str(p) for p in raw_allowed_paths if str(p).strip()
                            ]
                            if inferred_allowed and is_generic_allowed_paths(
                                allowed_paths
                            ):
                                allowed_paths = inferred_allowed
                        else:
                            allowed_paths = inferred_allowed or ["src/", "tests/"]
    
                        skills_used = (
                            task_data.get("suggested_claude_skills")
                            or task_data.get("suggested_skills")
                            or task_data.get("skills_used", [])
                        )
                        mcp_servers_used = task_data.get("suggested_mcp_servers") or task_data.get(
                            "mcp_servers_used", []
                        )
                        subagents_used = task_data.get("suggested_subagents") or task_data.get(
                            "subagents_used", []
                        )
                        estimated_complexity = task_data.get("estimated_complexity", "medium")
                        goal = task_data.get("goal", description)
                        acceptance_criteria = task_data.get("acceptance_criteria", [])
    
                        # Generate task file content
                        task_content = _generate_task_file(
                            task_id=global_task_id,
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
                        task_path = output_dir / f"{global_task_id}.md"
                        with open(task_path, "w") as f:
                            f.write(task_content)
    
                        # Parse the generated task file
                        parsed_task = parse_task_file(task_path)
                        all_tasks[global_task_id] = parsed_task
                        chunk_tasks.append(global_task_id)
                        
                        logger.info(
                            f"Chunk {chunk_num}: Task {global_task_id} - {title} ({estimated_complexity})"
                        )
                        

                    # Track task count for this chunk
                    chunk_task_counts.append(len(chunk_tasks))
                    # Add edges from this chunk
                    task_id_set = set(all_tasks.keys())
                    chunk_edges = _merge_edges(raw_edges, tasks_data, task_id_set)
                    global_edges.extend(chunk_edges)

                    if progress_callback:
                        progress_callback(
                            f"Chunk {chunk_num} completed: {len(chunk_tasks)} tasks"
                        )
                except Exception as e:
                    # Save chunk content for debugging
                    chunk_debug_path = output_dir / f".chunk_{chunk_num}_error.md"
                    try:
                        with open(chunk_debug_path, "w") as f:
                            f.write(f"# Chunk {chunk_num} ({chunk.id}) - FAILED\n\n")
                            f.write(f"## Error\n{e}\n\n")
                            f.write(f"## Chunk Content\n```\n{chunk_content}\n```\n")
                        logger.info(f"Saved chunk debug info to: {chunk_debug_path}")
                    except Exception:
                        pass

                    raise PlanExpanderError(
                        f"Failed to expand chunk {chunk_num} ({chunk.id}): {e}"
                    ) from e

            # Update task ID offset for next chunk
            task_id_offset += len(chunk_tasks)

        # Reconstruct task IDs from chunk_task_counts for cross-chunk dependencies
        task_ids_in_order = []
        offset = 1
        for count in chunk_task_counts:
            for i in range(count):
                task_ids_in_order.append(f"task-{offset + i}")
            offset += count

        # Add sequential dependencies between chunks
        # Last task of chunk N → First task of chunk N+1
        for i in range(len(chunk_task_counts) - 1):
            # Get the actual task IDs from the reconstructed list
            chunk_start_idx = sum(chunk_task_counts[:i])
            chunk_end_idx = sum(chunk_task_counts[: i + 1])

            if chunk_start_idx < chunk_end_idx and chunk_end_idx <= len(task_ids_in_order):
                # Last task of chunk i
                last_task_id = task_ids_in_order[chunk_end_idx - 1]

                # First task of chunk i+1
                next_chunk_start_idx = chunk_end_idx
                if next_chunk_start_idx < len(task_ids_in_order):
                    first_task_id = task_ids_in_order[next_chunk_start_idx]

                    if last_task_id in all_tasks and first_task_id in all_tasks:
                        global_edges.append((last_task_id, first_task_id))
                        logger.info(
                            f"Added cross-chunk dependency: {last_task_id} → {first_task_id}"
                        )

    # Build final DAG
    task_id_list = list(all_tasks.keys())
    topo_order, parallel_batches, cycle_nodes = _toposort_batches(task_id_list, global_edges)

    if cycle_nodes:
        raise PlanExpanderError(
            "Cycle detected in chunked task dependencies: "
            + ", ".join(sorted(cycle_nodes)[:20])
            + (" ..." if len(cycle_nodes) > 20 else "")
        )

    # Write final DAG artifact
    dag_artifact = Path(".autopilot/plan/dag.json")
    dag_artifact.parent.mkdir(parents=True, exist_ok=True)
    with open(dag_artifact, "w") as f:
        json.dump(
            {
                "tasks": list(all_tasks.keys()),
                "edges": global_edges,
                "topo_order": topo_order,
                "parallel_batches": parallel_batches,
            },
            f,
            indent=2,
        )

    logger.info(f"Plan expanded to {len(all_tasks)} tasks across {len(chunks)} chunks")

    return TaskDAG(
        tasks=all_tasks,
        edges=[tuple(edge) for edge in global_edges],
        topo_order=topo_order,
        parallel_batches=parallel_batches,
    )


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
    if not isinstance(validation_commands, dict):
        validation_commands = {}

    lines = [
        f"# Task: {title}",
        "",
        f"**Task ID:** {task_id}",
        f"**Estimated Complexity:** {estimated_complexity}",
        "",
    ]

    # Goal section
    lines.extend(
        [
            "## Goal",
            goal or description,
            "",
        ]
    )

    # Dependencies section
    if dependencies:
        lines.extend(
            [
                "## Dependencies",
                f"This task depends on: {', '.join(dependencies)}",
                "",
            ]
        )

    # Acceptance criteria section (always present)
    lines.extend(
        [
            "## Acceptance Criteria",
        ]
    )
    if acceptance_criteria:
        for i, criterion in enumerate(acceptance_criteria, 1):
            lines.append(f"- [ ] {criterion}")
    else:
        lines.extend(
            [
                "- [ ] Task completed according to description",
                "- [ ] Code follows project conventions",
                "- [ ] Tests pass",
            ]
        )
    lines.append("")

    # Constraints section
    lines.extend(
        [
            "## Constraints",
            "- Follow existing code patterns",
            "- Maintain backward compatibility",
            "",
        ]
    )

    # Allowed paths section
    lines.extend(
        [
            "## Allowed Paths",
        ]
    )
    for path in allowed_paths:
        lines.append(f"- {path}")
    lines.append("")

    # Enriched metadata section
    if skills_used or mcp_servers_used or subagents_used:
        lines.extend(
            [
                "## Agent Guidance",
            ]
        )

        if skills_used:
            lines.append(f"**Recommended Skills:** {', '.join(skills_used)}")
        if mcp_servers_used:
            lines.append(f"**MCP Servers:** {', '.join(mcp_servers_used)}")
        if subagents_used:
            lines.append(f"**Subagents:** {', '.join(subagents_used)}")

        lines.append("")

    # Validation commands section (optional). When omitted, runtime uses repo config defaults.
    commands = {k: v for k, v in (validation_commands or {}).items() if v}
    if commands:
        lines.extend(["## Validation Commands", "```yaml"])
        for key, value in commands.items():
            lines.append(f"{key}: {value}")
        lines.extend(["```", ""])

    # UAT section
    lines.extend(
        [
            "## User Acceptance Tests",
            "1. Verify the feature works as described",
            "2. Check edge cases",
            "3. Ensure proper error handling",
            "",
        ]
    )

    # Notes section
    notes = ["This task was auto-generated from a plan."]
    if estimated_complexity == "high" or estimated_complexity == "critical":
        notes.append(
            f"WARNING: This task is marked as {estimated_complexity.upper()} complexity - allow extra time and review."
        )

    lines.extend(
        [
            "## Notes",
        ]
    )
    for note in notes:
        lines.append(f"- {note}")
    lines.append("")

    return "\n".join(lines)


def _infer_allowed_paths(title: str, description: str, repo_root: Path) -> list[str]:
    """Infer allowed paths for a task when the planner doesn't provide any."""
    text_raw = f"{title}\n{description}"
    text = text_raw.lower()

    # Prefer explicit paths mentioned in the plan/task description.
    # Example: `src/autopilot_smoke/math_add.py` -> allow `src/autopilot_smoke/`.
    import re

    explicit: set[str] = set()
    for m in re.findall(r"(?:src|tests)/[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+", text):
        parts = m.split("/")
        if len(parts) >= 2:
            explicit.add(f"{parts[0]}/{parts[1]}/")
    # Also accept directory-only mentions like `src/foo/`.
    for m in re.findall(r"(?:src|tests)/[A-Za-z0-9_.-]+/", text):
        parts = m.split("/")
        if len(parts) >= 2:
            explicit.add(f"{parts[0]}/{parts[1]}/")
    if explicit:
        return sorted(explicit)

    frontend_dirs = ["frontend", "client", "web", "app"]
    backend_dirs = ["backend", "server", "api"]

    if any(
        k in text
        for k in ["vite", "react", "tailwind", "frontend", "ui", "screen", "mockup", "html"]
    ):
        for d in frontend_dirs:
            if (repo_root / d).exists():
                return [f"{d}/"]

    if any(k in text for k in ["fastapi", "backend", "api", "server"]):
        for d in backend_dirs:
            if (repo_root / d).exists():
                return [f"{d}/"]

    if (repo_root / "src").exists():
        return ["src/"]
    if (repo_root / "tests").exists():
        return ["tests/"]

    return []


def _edges_from_task_data(tasks_data: list[dict], task_ids: set[str]) -> list[tuple[str, str]]:
    """Extract edges from per-task dependency fields."""
    edges: set[tuple[str, str]] = set()
    for task_data in tasks_data:
        if not isinstance(task_data, dict):
            continue
        task_id = str(task_data.get("id") or "").strip()
        if not task_id or task_id not in task_ids:
            continue
        deps = task_data.get("depends_on") or task_data.get("dependencies", [])
        if isinstance(deps, str):
            deps = [deps]
        if not isinstance(deps, list):
            continue
        for dep in deps:
            dep_id = str(dep).strip()
            if not dep_id or dep_id == task_id:
                continue
            if dep_id not in task_ids:
                continue
            edges.add((dep_id, task_id))
    return sorted(edges)


def _normalize_edges(raw_edges: object, task_ids: set[str]) -> list[tuple[str, str]]:
    """Normalize planner edges into (from, to) tuples scoped to known task ids."""
    if not isinstance(raw_edges, list):
        return []

    edges: set[tuple[str, str]] = set()
    dropped_unknown = 0

    for edge in raw_edges:
        from_id: str | None = None
        to_id: str | None = None

        if isinstance(edge, (list, tuple)) and len(edge) == 2:
            from_id = str(edge[0]).strip()
            to_id = str(edge[1]).strip()
        elif isinstance(edge, dict):
            if "from" in edge and "to" in edge:
                from_id = str(edge.get("from")).strip()
                to_id = str(edge.get("to")).strip()
            elif "source" in edge and "target" in edge:
                from_id = str(edge.get("source")).strip()
                to_id = str(edge.get("target")).strip()
            elif "src" in edge and "dst" in edge:
                from_id = str(edge.get("src")).strip()
                to_id = str(edge.get("dst")).strip()

        if not from_id or not to_id:
            continue
        if from_id == to_id:
            continue
        if from_id not in task_ids or to_id not in task_ids:
            dropped_unknown += 1
            continue

        edges.add((from_id, to_id))

    if dropped_unknown:
        logger.warning("Dropped %s planner edges referencing unknown tasks", dropped_unknown)

    return sorted(edges)


def _merge_edges(
    raw_edges: object, tasks_data: list[dict], task_ids: set[str]
) -> list[tuple[str, str]]:
    """Merge edges from planner edge list and per-task dependency fields."""
    merged: set[tuple[str, str]] = set()
    merged.update(_normalize_edges(raw_edges, task_ids))
    merged.update(_edges_from_task_data(tasks_data, task_ids))
    return sorted(merged)


def _toposort_batches(
    task_ids_in_order: list[str],
    edges: list[tuple[str, str]],
) -> tuple[list[str], list[list[str]], set[str]]:
    """Compute a stable topo-order and parallel batches from tasks+edges.

    Returns:
        topo_order, parallel_batches, cycle_nodes
    """
    task_ids = [t for t in task_ids_in_order if t]
    task_set = set(task_ids)

    adjacency: dict[str, list[str]] = {t: [] for t in task_ids}
    indegree: dict[str, int] = {t: 0 for t in task_ids}

    for from_id, to_id in edges:
        if from_id not in task_set or to_id not in task_set:
            continue
        if from_id == to_id:
            continue
        adjacency[from_id].append(to_id)
        indegree[to_id] += 1

    for src in adjacency:
        adjacency[src].sort()

    topo_order: list[str] = []
    parallel_batches: list[list[str]] = []
    processed: set[str] = set()

    ready = [t for t in task_ids if indegree[t] == 0]
    while ready:
        batch = list(ready)
        parallel_batches.append(batch)
        topo_order.extend(batch)
        processed.update(batch)

        next_ready: set[str] = set()
        for node in batch:
            for neighbor in adjacency.get(node, []):
                indegree[neighbor] -= 1
                if indegree[neighbor] == 0:
                    next_ready.add(neighbor)

        ready = [t for t in task_ids if t in next_ready and t not in processed]

    cycle_nodes = task_set - processed
    return topo_order, parallel_batches, cycle_nodes


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

    task_ids = list(dag.tasks.keys())
    task_set = set(task_ids)

    topo_order = dag.topo_order or []
    topo_set: set[str] = set()
    duplicates: set[str] = set()
    for task_id in topo_order:
        if task_id in topo_set:
            duplicates.add(task_id)
        topo_set.add(task_id)

    for task_id in sorted(duplicates):
        errors.append(f"Task {task_id} appears multiple times in topological order")

    for task_id in task_set:
        if task_id not in topo_set:
            errors.append(f"Task {task_id} not in topological order")

    for task_id in topo_set:
        if task_id not in task_set:
            errors.append(f"Topological order references non-existent task: {task_id}")

    # Check that all edges reference valid tasks
    for from_id, to_id in dag.edges:
        if from_id not in dag.tasks:
            errors.append(f"Edge references non-existent task: {from_id}")
        if to_id not in dag.tasks:
            errors.append(f"Edge references non-existent task: {to_id}")

    # Check that topo order respects edges.
    index = {task_id: i for i, task_id in enumerate(topo_order)}
    for from_id, to_id in dag.edges:
        if from_id in index and to_id in index and index[from_id] >= index[to_id]:
            errors.append(f"Topological order violates dependency: {from_id} -> {to_id}")

    # Check for cycles accurately via Kahn's algorithm.
    edges_scoped = [(a, b) for (a, b) in dag.edges if a in task_set and b in task_set]
    _, _, cycle_nodes = _toposort_batches(task_ids, edges_scoped)
    if cycle_nodes:
        errors.append(
            "Cycle detected in task dependencies: "
            + ", ".join(sorted(cycle_nodes)[:20])
            + (" ..." if len(cycle_nodes) > 20 else "")
        )

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
