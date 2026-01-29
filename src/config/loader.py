"""Configuration loader with validation."""

from pathlib import Path

import yaml
from pydantic import ValidationError

from .models import AutopilotConfig


class ConfigError(Exception):
    """Configuration error."""

    pass


def load_config(config_path: Path) -> AutopilotConfig:
    """Load and validate configuration from YAML file.

    Args:
        config_path: Path to config YAML file

    Returns:
        Validated AutopilotConfig instance

    Raises:
        ConfigError: If config file missing or invalid
    """
    if not config_path.exists():
        raise ConfigError(f"Configuration file not found: {config_path}")

    try:
        with open(config_path, "r") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigError(f"Invalid YAML in {config_path}: {e}")

    if not data:
        raise ConfigError(f"Empty configuration file: {config_path}")

    # Resolve repo root relative to config file
    if "repo" in data and "root" in data["repo"]:
        root_path = Path(data["repo"]["root"])
        if not root_path.is_absolute():
            data["repo"]["root"] = (config_path.parent / root_path).resolve()

    try:
        return AutopilotConfig(**data)
    except ValidationError as e:
        raise ConfigError(f"Configuration validation failed: {e}")


def create_default_config(config_path: Path) -> None:
    """Create default configuration file.

    Args:
        config_path: Path where config should be created
    """
    config_path.parent.mkdir(parents=True, exist_ok=True)

    default_config = {
        "repo": {
            "root": str(Path.cwd()),
            "default_branch": "main",
            "remote": "origin",
        },
        "commands": {
            "format": "ruff format .",
            "lint": "ruff check .",
            "tests": "pytest -q",
            "uat": "pytest -q tests/uat",
        },
        "orchestrator": {
            "planner_timeout_sec": 300,
            "max_planner_retries": 1,
            "final_uat_timeout_sec": 300,
        },
        "loop": {
            "max_iterations": 10,
            "build_timeout_sec": 600,
            "validate_timeout_sec": 120,
            "review_timeout_sec": 180,
            "uat_generate_timeout_sec": 180,
            "uat_run_timeout_sec": 300,
            "stuck_no_output_sec": 120,
            "allow_no_tests": True,
        },
        "safety": {
            "allowed_paths": ["src/", "tests/"],
            "denied_paths": [],
            "diff_lines_cap": 1000,
            "max_todo_count": 0,
            "forbid_network_tools": False,
        },
        "reviewer": {
            "mode": "codex_cli",
            "model": None,
            "max_retries": 1,
            "disable_mcp": True,
        },
        "planner": {
            "mode": "codex_cli",
            "model": None,
            "disable_mcp": True,
        },
        "builder": {
            "cli_path": "claude",
            "max_retries": 1,
            "permission_mode": "bypassPermissions",
            "stream_output": True,
            "stream_log_interval_sec": 1.5,
            "system_prompt": None,
        },
        "github": {
            "enabled": False,
            "create_pr": False,
        },
        "logging": {
            "level": "INFO",
            "log_dir": ".autopilot/logs",
        },
    }

    with open(config_path, "w") as f:
        yaml.dump(default_config, f, default_flow_style=False, sort_keys=False)
