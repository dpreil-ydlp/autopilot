"""Task file parser."""

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ParsedTask:
    """Parsed task file."""

    task_id: str
    title: str
    goal: str
    acceptance_criteria: list[str]
    constraints: list[str]
    allowed_paths: list[str]
    validation_commands: dict[str, str]
    uat_instructions: list[str]
    notes: str
    raw_content: str


class TaskParseError(Exception):
    """Task parsing error."""

    pass


def parse_task_file(task_path: Path) -> ParsedTask:
    """Parse task file from Markdown.

    Args:
        task_path: Path to task markdown file

    Returns:
        ParsedTask object

    Raises:
        TaskParseError: If file format is invalid
    """
    if not task_path.exists():
        raise TaskParseError(f"Task file not found: {task_path}")

    # Generate task_id from filename
    task_id = task_path.stem

    with open(task_path, "r") as f:
        content = f.read()

    # Parse title from first heading
    title_match = re.search(r"^#\s+Task:\s*(.+)$", content, re.MULTILINE)
    if not title_match:
        raise TaskParseError(f"Missing title in task file: {task_path}")
    title = title_match.group(1).strip()

    # Parse sections
    goal = _extract_section(content, "Goal", required=True)
    acceptance_criteria = _extract_list_section(content, "Acceptance Criteria", required=True)
    constraints = _extract_list_section(content, "Constraints", required=False)
    allowed_paths = _extract_list_section(content, "Allowed Paths", required=False)
    validation_commands = _extract_validation_commands(content)
    uat_instructions = _extract_list_section(content, "User Acceptance Tests", required=False)
    notes = _extract_section(content, "Notes", required=False)

    logger.info(f"Parsed task: {task_id} - {title}")

    return ParsedTask(
        task_id=task_id,
        title=title,
        goal=goal,
        acceptance_criteria=acceptance_criteria,
        constraints=constraints or [],
        allowed_paths=allowed_paths or [],
        validation_commands=validation_commands,
        uat_instructions=uat_instructions or [],
        notes=notes,
        raw_content=content,
    )


def _extract_section(content: str, section_name: str, required: bool = True) -> str:
    """Extract section content from markdown.

    Args:
        content: Full markdown content
        section_name: Name of section to extract
        required: Whether section must exist

    Returns:
        Section content (empty string if not found and not required)

    Raises:
        TaskParseError: If required section not found
    """
    # Match ## Section Name ... (until next ## or EOF)
    pattern = rf"^##\s+{re.escape(section_name)}\s*$\n(.*?)(?=^##\s|\Z)"
    match = re.search(pattern, content, re.MULTILINE | re.DOTALL)

    if not match:
        if required:
            raise TaskParseError(f"Missing required section: {section_name}")
        return ""

    section_text = match.group(1).strip()

    # Remove HTML comments like <!-- ... -->
    section_text = re.sub(r"<!--.*?-->", "", section_text, flags=re.DOTALL)

    return section_text


def _extract_list_section(content: str, section_name: str, required: bool = False) -> list[str]:
    """Extract list items from a section.

    Args:
        content: Full markdown content
        section_name: Name of section to extract
        required: Whether section must exist

    Returns:
        List of items (empty if not found and not required)

    Raises:
        TaskParseError: If required section not found
    """
    section_text = _extract_section(content, section_name, required)

    if not section_text:
        return []

    # Extract list items (bullets or numbered)
    items = []
    for line in section_text.split("\n"):
        line = line.strip()
        # Match bullets (- or *) or numbered lists (1. 2. etc.)
        if line.startswith(("- ", "* ")) or re.match(r"^\d+\.\s", line):
            # Remove bullet/number and any checkbox markers
            item = re.sub(r"^[-*]\s*\[[ x]\]?\s*", "", line)
            item = re.sub(r"^\d+\.\s*", "", item)
            item = item.strip()
            if item:
                items.append(item)

    return items


def _extract_validation_commands(content: str) -> dict[str, str]:
    """Extract validation commands from code block.

    Args:
        content: Full markdown content

    Returns:
        Dict of command names to command strings
    """
    section_text = _extract_section(content, "Validation Commands", required=False)

    if not section_text:
        # Return default commands
        return {
            "tests": "pytest -q",
        }

    # Try to extract YAML code block
    yaml_match = re.search(r"```yaml\n(.*?)```", section_text, re.DOTALL)

    if yaml_match:
        try:
            import yaml

            commands = yaml.safe_load(yaml_match.group(1))
            if isinstance(commands, dict):
                return commands
        except Exception:
            logger.warning("Failed to parse validation commands as YAML")

    # Fall back to parsing as simple list
    commands = {}
    for line in section_text.split("\n"):
        line = line.strip()
        if ":" in line and not line.startswith("#"):
            key, value = line.split(":", 1)
            commands[key.strip()] = value.strip()

    # Ensure at least tests command exists
    if "tests" not in commands:
        commands["tests"] = "pytest -q"

    return commands


def validate_task_constraints(task: ParsedTask) -> list[str]:
    """Validate task constraints and return list of violations.

    Args:
        task: Parsed task

    Returns:
        List of violation messages (empty if valid)
    """
    violations = []

    # Check that acceptance criteria exist
    if not task.acceptance_criteria:
        violations.append("Task must have at least one acceptance criterion")

    # Check that allowed paths are specified
    if not task.allowed_paths:
        violations.append("Task should specify allowed paths for safety")

    # Check that validation commands include tests
    if "tests" not in task.validation_commands:
        violations.append("Task must have tests validation command")

    return violations
