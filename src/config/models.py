"""Configuration models for Autopilot."""

from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class RepoConfig(BaseModel):
    """Repository configuration."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    root: Path = Field(description="Root directory of the repository")
    default_branch: str = Field(default="main", description="Default branch name")
    remote: Optional[str] = Field(default=None, description="Remote name")


class CommandsConfig(BaseModel):
    """Validation and UAT commands."""

    format: Optional[str] = Field(default=None, description="Format command (optional)")
    lint: Optional[str] = Field(default=None, description="Lint command (optional)")
    tests: str = Field(description="Test command (required)")
    uat: Optional[str] = Field(default=None, description="UAT command (optional)")


class OrchestratorConfig(BaseModel):
    """Orchestrator-level timeouts and limits."""

    planner_timeout_sec: int = Field(default=300, description="Planner timeout")
    max_planner_retries: int = Field(default=1, description="Max planner retries")
    final_uat_timeout_sec: int = Field(default=300, description="Final UAT timeout")


class LoopConfig(BaseModel):
    """Worker loop configuration."""

    max_iterations: int = Field(default=10, description="Max build-review-fix iterations")
    build_timeout_sec: int = Field(default=600, description="Build timeout")
    validate_timeout_sec: int = Field(default=120, description="Validate timeout")
    review_timeout_sec: int = Field(default=180, description="Review timeout")
    uat_generate_timeout_sec: int = Field(default=180, description="UAT generation timeout")
    uat_run_timeout_sec: int = Field(default=300, description="UAT run timeout")
    stuck_no_output_sec: int = Field(default=120, description="Stuck detection threshold")
    allow_no_tests: bool = Field(
        default=True,
        description="Treat 'no tests ran' as success for pytest",
    )


class SafetyConfig(BaseModel):
    """Safety guards and constraints."""

    allowed_paths: list[str] = Field(default_factory=list, description="Allowed file paths")
    denied_paths: list[str] = Field(default_factory=list, description="Denied file paths")
    diff_lines_cap: int = Field(default=1000, description="Max diff lines")
    max_todo_count: int = Field(default=0, description="Max new TODOs allowed (0 = none)")
    forbid_network_tools: bool = Field(default=False, description="Forbid network tools")


class ReviewerConfig(BaseModel):
    """Reviewer agent configuration."""

    mode: str = Field(default="codex_cli", description="codex_cli or openai_api")
    model: Optional[str] = Field(
        default=None,
        description="Model for openai_api mode (falls back to OPENAI_MODEL)",
    )
    api_key_env: Optional[str] = Field(default=None, description="API key env var name")
    max_retries: int = Field(default=1, description="Max review retries")
    disable_mcp: bool = Field(
        default=True,
        description="Disable MCP server startup for Codex CLI runs",
    )
    json_schema_path: str = Field(default="schemas/review.json", description="Review schema path")


class PlannerConfig(BaseModel):
    """Planner agent configuration."""

    mode: str = Field(default="codex_cli", description="codex_cli or openai_api")
    model: Optional[str] = Field(
        default=None,
        description="Model for openai_api mode (falls back to OPENAI_MODEL)",
    )
    api_key_env: Optional[str] = Field(default=None, description="API key env var name")
    disable_mcp: bool = Field(
        default=True,
        description="Disable MCP server startup for Codex CLI runs",
    )
    json_schema_path: str = Field(default="schemas/plan.json", description="Plan schema path")


class BuilderConfig(BaseModel):
    """Builder agent configuration."""

    cli_path: str = Field(default="claude", description="Claude Code CLI path")
    max_retries: int = Field(default=1, description="Max build retries")
    allowed_skills: list[str] = Field(default_factory=list, description="Allowed skills")
    permission_mode: str = Field(
        default="bypassPermissions",
        description="Claude permission mode",
    )
    stream_output: bool = Field(default=True, description="Stream Claude output")
    stream_log_interval_sec: float = Field(
        default=1.5, description="Seconds between streamed log flushes"
    )
    system_prompt: Optional[str] = Field(default=None, description="Builder system prompt override")


class GitHubConfig(BaseModel):
    """GitHub integration configuration."""

    enabled: bool = Field(default=False, description="Enable GitHub integration")
    create_pr: bool = Field(default=False, description="Create PR after push")
    remote_name: str = Field(default="origin", description="Git remote name")
    base_branch: str = Field(default="main", description="PR base branch")
    final_gate_check: bool = Field(default=False, description="Require CI check before merge")


class LoggingConfig(BaseModel):
    """Logging configuration."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    level: str = Field(default="INFO", description="Log level")
    log_dir: Path = Field(default=Path(".autopilot/logs"), description="Log directory")
    rotation_mb: int = Field(default=10, description="Log rotation size (MB)")
    retention_days: int = Field(default=7, description="Log retention days")


class AutopilotConfig(BaseModel):
    """Main configuration model."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    repo: RepoConfig
    commands: CommandsConfig
    orchestrator: OrchestratorConfig = Field(default_factory=OrchestratorConfig)
    loop: LoopConfig = Field(default_factory=LoopConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    reviewer: ReviewerConfig = Field(default_factory=ReviewerConfig)
    planner: PlannerConfig = Field(default_factory=PlannerConfig)
    builder: BuilderConfig = Field(default_factory=BuilderConfig)
    github: GitHubConfig = Field(default_factory=GitHubConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
