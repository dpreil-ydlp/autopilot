"""Unit tests for configuration models."""

import pytest
from pathlib import Path
from pydantic import ValidationError

from src.config.models import AutopilotConfig, RepoConfig, CommandsConfig


def test_repo_config_defaults():
    """Test RepoConfig default values."""
    config = RepoConfig(root=Path("/tmp"))
    assert config.root == Path("/tmp")
    assert config.default_branch == "main"
    assert config.remote is None


def test_repo_config_custom():
    """Test RepoConfig with custom values."""
    config = RepoConfig(
        root=Path("/tmp/repo"),
        default_branch="develop",
        remote="upstream",
    )
    assert config.root == Path("/tmp/repo")
    assert config.default_branch == "develop"
    assert config.remote == "upstream"


def test_commands_config_required():
    """Test CommandsConfig requires tests field."""
    config = CommandsConfig(tests="pytest -q")
    assert config.tests == "pytest -q"
    assert config.format is None
    assert config.lint is None
    assert config.uat is None


def test_commands_config_full():
    """Test CommandsConfig with all fields."""
    config = CommandsConfig(
        format="ruff format .",
        lint="ruff check .",
        tests="pytest -q",
        uat="pytest -q tests/uat",
    )
    assert config.format == "ruff format ."
    assert config.lint == "ruff check ."
    assert config.tests == "pytest -q"
    assert config.uat == "pytest -q tests/uat"


def test_autopilot_config_minimal():
    """Test AutopilotConfig with minimal required fields."""
    config = AutopilotConfig(
        repo=RepoConfig(root=Path("/tmp")),
        commands=CommandsConfig(tests="pytest"),
    )
    assert config.repo.root == Path("/tmp")
    assert config.commands.tests == "pytest"
    assert config.loop.max_iterations == 10  # Default


def test_autopilot_config_with_safety():
    """Test AutopilotConfig with safety constraints."""
    config = AutopilotConfig(
        repo=RepoConfig(root=Path("/tmp")),
        commands=CommandsConfig(tests="pytest"),
        safety={
            "allowed_paths": ["src/", "tests/"],
            "denied_paths": ["production/"],
            "diff_lines_cap": 500,
            "max_todo_count": 5,
        },
    )
    assert len(config.safety.allowed_paths) == 2
    assert len(config.safety.denied_paths) == 1
    assert config.safety.diff_lines_cap == 500
    assert config.safety.max_todo_count == 5
