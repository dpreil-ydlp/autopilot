"""Unit tests for task and plan processing."""

from pathlib import Path

import pytest

from src.tasks.chunker import PlanChunker, PlanSection
from src.tasks.parser import (
    TaskParseError,
    parse_task_file,
    validate_task_constraints,
)
from src.tasks.plan import (
    TaskDAG,
    _generate_task_file,
    _infer_allowed_paths,
    compute_ready_set,
    validate_dag,
)


@pytest.fixture
def sample_task_file(tmp_path):
    """Create a sample task file."""
    task_path = tmp_path / "sample-task.md"
    content = """# Task: Sample Task

## Goal
This is a sample task for testing.

## Acceptance Criteria
- Criterion 1
- Criterion 2

## Constraints
- No constraints

## Allowed Paths
- src/
- tests/

## Validation Commands
```yaml
tests: pytest -q
lint: ruff check .
format: ruff format .
```

## User Acceptance Tests
1. Test the feature
2. Check edge cases

## Notes
This is a note.
"""
    task_path.write_text(content)
    return task_path


def test_parse_task_file(sample_task_file):
    """Test parsing a valid task file."""
    task = parse_task_file(sample_task_file)

    assert task.task_id == "sample-task"
    assert task.title == "Sample Task"
    assert task.goal == "This is a sample task for testing."
    assert len(task.acceptance_criteria) == 2
    assert len(task.constraints) == 1
    # Note: allowed_paths includes the bullet prefix in the raw parsing
    assert len(task.allowed_paths) == 2
    assert task.validation_commands["tests"] == "pytest -q"
    assert task.validation_commands["lint"] == "ruff check ."
    assert task.validation_commands["format"] == "ruff format ."
    assert len(task.uat_instructions) == 2
    assert "This is a note" in task.notes


def test_parse_task_file_missing_title(tmp_path):
    """Test parsing task file without title fails."""
    task_path = tmp_path / "no-title.md"
    task_path.write_text("# No Title\n\nSome content")

    with pytest.raises(TaskParseError, match="Missing title"):
        parse_task_file(task_path)


def test_parse_task_file_missing_section(tmp_path):
    """Test parsing task file without required section fails."""
    task_path = tmp_path / "no-goal.md"
    task_path.write_text("# Task: Test\n\n## Acceptance Criteria\n- None")

    with pytest.raises(TaskParseError, match="Missing required section: Goal"):
        parse_task_file(task_path)


def test_parse_task_file_not_found():
    """Test parsing non-existent task file fails."""
    with pytest.raises(TaskParseError, match="Task file not found"):
        parse_task_file(Path("/nonexistent/task.md"))


def test_validate_task_constraints_valid(sample_task_file):
    """Test validation of valid task passes."""
    task = parse_task_file(sample_task_file)
    violations = validate_task_constraints(task)

    assert len(violations) == 0


def test_validate_task_no_acceptance_criteria(tmp_path):
    """Test validation fails without acceptance criteria."""
    task_path = tmp_path / "no-ac.md"
    task_path.write_text("# Task: Test\n\n## Goal\nTest\n\n## Acceptance Criteria\n")
    task = parse_task_file(task_path)

    violations = validate_task_constraints(task)

    assert len(violations) > 0
    assert any("acceptance criterion" in v.lower() for v in violations)


def test_generate_task_file():
    """Test task file generation."""
    content = _generate_task_file(
        task_id="task-1",
        title="Test Task",
        description="Test description",
        dependencies=["task-0"],
    )

    assert "# Task: Test Task" in content
    assert "task-1" in content
    assert "Test description" in content
    assert "task-0" in content
    assert "## Acceptance Criteria" in content


def test_infer_allowed_paths_prefers_explicit_paths(tmp_path):
    """Infer allowed_paths from explicit file path mentions in task text."""
    repo_root = tmp_path
    title = "Smoke task"
    description = (
        "Add src/autopilot_smoke/math_add.py and tests/autopilot_smoke/test_math_add.py. "
        "Only touch src/autopilot_smoke/ and tests/autopilot_smoke/."
    )
    allowed = _infer_allowed_paths(title=title, description=description, repo_root=repo_root)
    assert allowed == ["src/autopilot_smoke/", "tests/autopilot_smoke/"]


def test_compute_ready_set():
    """Test computing ready set from DAG."""
    # Create simple DAG: task1 -> task2 -> task3
    dag = TaskDAG(
        tasks={},
        edges=[("task1", "task2"), ("task2", "task3")],
        topo_order=["task1", "task2", "task3"],
        parallel_batches=[],
    )

    # Initially, only task1 is ready
    ready = compute_ready_set(dag, set())
    assert ready == {"task1"}

    # After completing task1, task2 becomes ready
    ready = compute_ready_set(dag, {"task1"})
    assert ready == {"task2"}

    # After completing task2, task3 becomes ready
    ready = compute_ready_set(dag, {"task1", "task2"})
    assert ready == {"task3"}

    # All completed
    ready = compute_ready_set(dag, {"task1", "task2", "task3"})
    assert ready == set()


def test_validate_dag_valid():
    """Test validation of valid DAG passes."""
    dag = TaskDAG(
        tasks={"task1": None, "task2": None},
        edges=[("task1", "task2")],
        topo_order=["task1", "task2"],
        parallel_batches=[["task1"], ["task2"]],
    )

    errors = validate_dag(dag)
    assert len(errors) == 0


def test_validate_dag_invalid_edge():
    """Test validation detects invalid edge."""
    dag = TaskDAG(
        tasks={"task1": None},
        edges=[("task1", "nonexistent")],  # Invalid edge
        topo_order=["task1"],
        parallel_batches=[],
    )

    errors = validate_dag(dag)
    assert len(errors) > 0
    assert any("non-existent task" in e for e in errors)


def test_chunker_small_plan_no_chunking(tmp_path):
    """Test that small plans don't get chunked."""
    plan_path = tmp_path / "small_plan.md"
    plan_path.write_text(
        """# Plan: Small Plan

## Overview
A small plan that doesn't need chunking.

## Tasks
1. Task one
2. Task two
"""
    )

    chunker = PlanChunker(plan_path)
    metadata = chunker.analyze()

    assert metadata["needs_chunking"] is False
    assert metadata["total_tokens"] < PlanChunker.CHUNK_TARGET_TOKENS


def test_chunker_large_plan_needs_chunking(tmp_path):
    """Test that large plans get chunked."""
    plan_path = tmp_path / "large_plan.md"

    # Create a large plan (3000+ tokens)
    sections = ["# Plan: Large Plan\n\n## Overview\n"]
    for i in range(100):
        sections.append(f"\n### Section {i}\n")
        sections.append("Content " * 50)  # ~300 tokens per section

    plan_path.write_text("".join(sections))

    chunker = PlanChunker(plan_path)
    metadata = chunker.analyze()

    assert metadata["needs_chunking"] is True
    assert metadata["total_tokens"] > PlanChunker.CHUNK_TARGET_TOKENS
    assert len(metadata["sections"]) > 0


def test_chunker_extracts_sections(tmp_path):
    """Test that chunker extracts markdown sections correctly."""
    plan_path = tmp_path / "sections.md"
    plan_path.write_text(
        """# Plan: Test

## Overview
This is the overview.

## Task 1
First task details.

### Subsection
More details.

## Task 2
Second task details.
"""
    )

    chunker = PlanChunker(plan_path)
    metadata = chunker.analyze()

    assert len(metadata["sections"]) >= 3  # Overview, Task 1, Task 2

    # Check section properties
    overview = next((s for s in metadata["sections"] if s.title == "Overview"), None)
    assert overview is not None
    assert overview.level == 2
    assert "overview" in overview.content.lower()


def test_chunker_creates_chunks_within_limits(tmp_path):
    """Test that created chunks respect token limits."""
    plan_path = tmp_path / "large_plan.md"

    # Create content that will be chunked
    sections = ["# Plan: Large\n\n"]
    for i in range(50):
        sections.append(f"\n## Section {i}\n")
        sections.append("A" * 200)  # Add content

    plan_path.write_text("".join(sections))

    chunker = PlanChunker(plan_path)
    metadata = chunker.analyze()
    chunks = chunker.create_chunks(metadata)

    # Verify all chunks are within limits
    for chunk in chunks:
        assert chunk.estimated_tokens <= PlanChunker.CHUNK_MAX_TOKENS

    # Verify dependencies are sequential
    for i, chunk in enumerate(chunks):
        if i == 0:
            assert chunk.dependencies == []
        else:
            assert chunk.dependencies == [f"chunk-{i}"]


def test_chunker_writes_chunk_files(tmp_path):
    """Test that chunks can be written to files."""
    plan_path = tmp_path / "plan.md"
    plan_path.write_text(
        """# Plan

## Section 1
Content 1

## Section 2
Content 2
"""
    )

    chunker = PlanChunker(plan_path)
    metadata = chunker.analyze()
    chunks = chunker.create_chunks(metadata)

    # Write first chunk
    if chunks:
        output_path = tmp_path / "chunk_1.md"
        chunker.write_chunk(chunks[0], output_path)

        assert output_path.exists()
        content = output_path.read_text()
        assert "Section 1" in content or "Section 2" in content
