"""Autopilot CLI entrypoint."""

import asyncio
import os
import sys
from pathlib import Path

import click

from .config.loader import ConfigError, create_default_config, load_config
from .executor.loop import ExecutionLoop
from .utils.cleanup import (
    cleanup_codex_home,
    cleanup_codex_processes,
    cleanup_codex_temp,
    cleanup_logs,
    cleanup_worktrees,
)
from .utils.logging import setup_logging

CONTEXT_SETTINGS = {
    "help_option_names": ["-h", "--help"],
}


@click.group(context_settings=CONTEXT_SETTINGS)
@click.option(
    "--config",
    "-c",
    default=".autopilot/config.yml",
    help="Path to configuration file",
    type=click.Path(exists=False, path_type=Path),
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Enable verbose output",
)
@click.pass_context
def cli(ctx: click.Context, config: Path, verbose: bool) -> None:
    """Autopilot - Local-first controller orchestrating builder + reviewer loop."""
    # Setup logging
    log_level = "DEBUG" if verbose else "INFO"
    setup_logging(level=log_level)

    # Initialize context
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config
    ctx.obj["verbose"] = verbose


@cli.command()
@click.option(
    "--force",
    "-f",
    is_flag=True,
    help="Overwrite existing configuration",
)
@click.pass_context
def init(ctx: click.Context, force: bool) -> None:
    """Initialize Autopilot configuration."""
    config_path: Path = ctx.obj["config_path"]

    if config_path.exists() and not force:
        click.echo(f"Configuration already exists: {config_path}")
        click.echo("Use --force to overwrite")
        sys.exit(1)

    try:
        create_default_config(config_path)
        click.echo(f"✓ Created configuration: {config_path}")
        click.echo("\nNext steps:")
        click.echo("  1. Review and customize .autopilot/config.yml")
        click.echo("  2. Create a task file in tasks/")
        click.echo("  3. Run: autopilot run tasks/<your-task>.md")
    except Exception as e:
        click.echo(f"✗ Failed to create configuration: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.argument("target", required=False, type=click.Path(exists=True, path_type=Path))
@click.option(
    "--queue",
    "-q",
    is_flag=True,
    help="Run tasks in directory as queue",
)
@click.option(
    "--pattern",
    "-p",
    help="Filter tasks by pattern",
)
@click.option(
    "--plan",
    type=click.Path(exists=True, path_type=Path),
    help="Run from plan file",
)
@click.option(
    "--max-workers",
    "-w",
    default=4,
    type=int,
    help="Maximum parallel workers",
)
@click.option(
    "--resume",
    is_flag=True,
    help="Resume from interrupted run",
)
@click.option(
    "--quiet",
    is_flag=True,
    help="Minimal output",
)
@click.pass_context
def run(
    ctx: click.Context,
    target: Path | None,
    queue: bool,
    pattern: str | None,
    plan: Path | None,
    max_workers: int,
    resume: bool,
    quiet: bool,
) -> None:
    """Run Autopilot on task or plan."""
    config_path: Path = ctx.obj["config_path"]
    verbose: bool = ctx.obj["verbose"]

    # Load config
    try:
        config = load_config(config_path)
        click.echo(f"✓ Configuration loaded: {config_path}")
    except ConfigError as e:
        click.echo(f"✗ Configuration error: {e}", err=True)
        sys.exit(1)

    # Reconfigure logging with file output
    setup_logging(
        level=config.logging.level,
        log_dir=config.logging.log_dir,
        rotation_mb=config.logging.rotation_mb,
        retention_days=config.logging.retention_days,
        use_colors=verbose,
        console=verbose and not quiet,
    )

    if not quiet:
        # Autopilot may use a different Codex model/effort than the user's interactive defaults.
        # Make this explicit once at startup to avoid confusion.
        def codex_notice(name: str, cfg) -> str | None:
            if getattr(cfg, "mode", None) != "codex_cli":
                return None
            model = getattr(cfg, "model", None)
            effort = getattr(cfg, "model_reasoning_effort", None)
            disable_mcp = bool(getattr(cfg, "disable_mcp", False))
            codex_home = getattr(cfg, "codex_home", None) or os.environ.get("AUTOPILOT_CODEX_HOME")
            parts = [f"{name}=codex_cli"]
            if model:
                parts.append(f"model={model}")
            if effort:
                parts.append(f"effort={effort}")
            if disable_mcp:
                parts.append("mcp=off")
                parts.append(f"home={codex_home or '.autopilot/codex-home'}")
            return " ".join(parts)

        notices = [
            codex_notice("planner", config.planner),
            codex_notice("reviewer", config.reviewer),
        ]
        msg = "; ".join([n for n in notices if n])
        if msg:
            click.echo(f"Codex: {msg}")

    # Run async execution loop
    success = asyncio.run(
        _run_async(
            config=config,
            target=target,
            queue=queue,
            pattern=pattern,
            plan=plan,
            max_workers=max_workers,
            resume=resume,
            verbose=verbose,
            quiet=quiet,
        )
    )

    sys.exit(0 if success else 1)


async def _run_async(
    config,
    target: Path | None,
    queue: bool,
    pattern: str | None,
    plan: Path | None,
    max_workers: int,
    resume: bool,
    verbose: bool,
    quiet: bool,
) -> bool:
    """Async run implementation.

    Args:
        config: Autopilot configuration
        target: Target task/plan file
        queue: Run as queue
        pattern: Pattern filter
        plan: Plan file
        max_workers: Max parallel workers
        resume: Resume flag
        verbose: Verbose output

    Returns:
        True if execution succeeded
    """
    loop = ExecutionLoop(
        config=config,
        verbose=verbose,
    )

    # Handle resume
    if resume:
        return await loop.resume()

    # Handle plan execution
    if plan:
        click.echo(f"Running plan: {plan}")
        return await loop.run_plan(plan, max_workers=max_workers)

    # Handle queue execution
    if queue:
        click.echo(f"Running tasks in queue: {target}")

        if not target or not target.is_dir():
            click.echo("Queue mode requires a directory")
            return False

        # Collect task files
        task_files = sorted(target.glob("*.md"))

        if not task_files:
            click.echo(f"No task files found in {target}")
            return False

        # Apply pattern filter if provided
        if pattern:
            import re

            regex = re.compile(pattern)
            task_files = [f for f in task_files if regex.search(f.name)]

        click.echo(f"Found {len(task_files)} tasks to run")

        # Run tasks sequentially
        all_success = True
        for i, task_file in enumerate(task_files, 1):
            click.echo(f"\n[{i}/{len(task_files)}] Running: {task_file.name}")

            success = await loop.run_single_task(task_file)

            if not success:
                click.echo(f"✗ Task failed: {task_file.name}")
                all_success = False
                break  # Stop on first failure
            else:
                click.echo(f"✓ Task completed: {task_file.name}")

        return all_success

    # Handle single task execution
    if target:
        click.echo(f"Running task: {target}")
        return await loop.run_single_task(target)

    # No target specified
    click.echo("No task or plan specified")
    click.echo("Usage: autopilot run <task.md> --plan <plan.md>")
    return False


@cli.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show current Autopilot status."""
    state_path = Path(".autopilot/state.json")

    if not state_path.exists():
        click.echo("No Autopilot run in progress")
        return

    import json

    with open(state_path) as f:
        state = json.load(f)

    click.echo(f"Run ID: {state.get('run_id', 'unknown')}")
    click.echo(f"State: {state.get('state', 'unknown')}")
    click.echo(f"Current Task: {state.get('current_task_id', 'none')}")
    click.echo(f"Created: {state.get('created_at', 'unknown')}")
    click.echo(f"Updated: {state.get('updated_at', 'unknown')}")

    if state.get("error_message"):
        click.echo(f"\nError: {state['error_message']}")


@cli.command()
@click.argument("task", required=False, type=click.Path(exists=False, path_type=Path))
@click.option(
    "--max-workers",
    "-w",
    default=4,
    type=int,
    help="Maximum parallel workers",
)
@click.pass_context
def resume(ctx: click.Context, task: Path | None, max_workers: int) -> None:
    """Resume interrupted Autopilot run.

    TASK: Optional path to task file (auto-detected if not specified)
    """
    config_path: Path = ctx.obj["config_path"]
    verbose: bool = ctx.obj["verbose"]

    # Load config
    try:
        config = load_config(config_path)
    except ConfigError as e:
        click.echo(f"✗ Configuration error: {e}", err=True)
        sys.exit(1)

    # Reconfigure logging with file output
    setup_logging(
        level=config.logging.level,
        log_dir=config.logging.log_dir,
        rotation_mb=config.logging.rotation_mb,
        retention_days=config.logging.retention_days,
        use_colors=verbose,
        console=verbose,
    )

    # Run async resume
    success = asyncio.run(_resume_async(config, task, max_workers, verbose))
    sys.exit(0 if success else 1)


async def _resume_async(config, task: Path | None, max_workers: int, verbose: bool) -> bool:
    """Async resume implementation.

    Args:
        config: Autopilot configuration
        task: Optional task file to resume
        max_workers: Max parallel workers
        verbose: Verbose output

    Returns:
        True if resume succeeded
    """
    loop = ExecutionLoop(config=config, verbose=verbose)

    # Try to resume from state (will auto-detect task if task not provided)
    return await loop.resume(task_path=task)


@cli.command()
@click.pass_context
def stop(ctx: click.Context) -> None:
    """Stop Autopilot at safe boundary."""
    stop_file = Path(".autopilot/AUTOPILOT_STOP")

    if stop_file.exists():
        click.echo("Stop already requested")
        return

    stop_file.parent.mkdir(parents=True, exist_ok=True)
    stop_file.touch()
    click.echo("✓ Stop requested - Autopilot will halt at next safe boundary")


@cli.command()
@click.pass_context
def pause(ctx: click.Context) -> None:
    """Pause Autopilot before next transition."""
    pause_file = Path(".autopilot/AUTOPILOT_PAUSE")

    if pause_file.exists():
        click.echo("Already paused")
        return

    pause_file.parent.mkdir(parents=True, exist_ok=True)
    pause_file.touch()
    click.echo("✓ Pause requested - Autopilot will pause before next transition")


@cli.command()
@click.pass_context
def unpause(ctx: click.Context) -> None:
    """Unpause paused Autopilot run."""
    pause_file = Path(".autopilot/AUTOPILOT_PAUSE")

    if not pause_file.exists():
        click.echo("Not paused")
        return

    pause_file.unlink()
    click.echo("✓ Unpaused - Autopilot will continue")


@cli.command()
@click.option(
    "--codex-temp/--no-codex-temp",
    default=True,
    help="Remove legacy codex-nomcp temp directories",
)
@click.option(
    "--worktrees",
    is_flag=True,
    help="Remove inactive worktrees under .autopilot/worktrees",
)
@click.option(
    "--logs",
    is_flag=True,
    help="Remove .autopilot/logs directory",
)
@click.option(
    "--codex-home",
    is_flag=True,
    help="Remove isolated codex home (AUTOPILOT_CODEX_HOME or .autopilot/codex-home)",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="List paths that would be removed without deleting anything",
)
@click.pass_context
def clean(
    ctx: click.Context,
    codex_temp: bool,
    worktrees: bool,
    logs: bool,
    codex_home: bool,
    dry_run: bool,
) -> None:
    """Clean up Autopilot temporary files."""
    config_path: Path = ctx.obj["config_path"]

    # Resolve repo root (best-effort).
    repo_root = Path.cwd()
    try:
        config = load_config(config_path)
        repo_root = config.repo.root
    except Exception:
        pass

    removed: list[Path] = []
    errors: list[str] = []

    if codex_temp:
        result = cleanup_codex_temp(dry_run=dry_run)
        removed.extend(result.removed)
        errors.extend(result.errors)

    if worktrees:
        result = cleanup_worktrees(repo_root=repo_root, dry_run=dry_run)
        removed.extend(result.removed)
        errors.extend(result.errors)

    if logs:
        result = cleanup_logs(repo_root=repo_root, dry_run=dry_run)
        removed.extend(result.removed)
        errors.extend(result.errors)

    if codex_home:
        result = cleanup_codex_home(repo_root=repo_root, dry_run=dry_run)
        removed.extend(result.removed)
        errors.extend(result.errors)

    if removed:
        click.echo("\nRemoved:")
        for path in removed:
            click.echo(f"  {path}")
    else:
        click.echo("No paths removed")

    if errors:
        click.echo("\nErrors:")
        for error in errors:
            click.echo(f"  {error}")


@cli.command()
@click.option(
    "--kill-codex/--no-kill-codex",
    default=True,
    help="Terminate running Codex CLI processes",
)
@click.option(
    "--codex-temp/--no-codex-temp",
    default=True,
    help="Remove legacy codex-nomcp temp directories",
)
@click.option(
    "--worktrees/--no-worktrees",
    default=True,
    help="Remove inactive worktrees under .autopilot/worktrees",
)
@click.option(
    "--logs/--no-logs",
    default=True,
    help="Remove .autopilot/logs directory",
)
@click.option(
    "--codex-home",
    is_flag=True,
    help="Remove isolated codex home (AUTOPILOT_CODEX_HOME or .autopilot/codex-home)",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="List actions without deleting or terminating processes",
)
@click.pass_context
def recover(
    ctx: click.Context,
    kill_codex: bool,
    codex_temp: bool,
    worktrees: bool,
    logs: bool,
    codex_home: bool,
    dry_run: bool,
) -> None:
    """Recover disk space and memory by cleaning temporary data."""
    config_path: Path = ctx.obj["config_path"]

    # Resolve repo root (best-effort).
    repo_root = Path.cwd()
    try:
        config = load_config(config_path)
        repo_root = config.repo.root
    except Exception:
        pass

    removed: list[Path] = []
    errors: list[str] = []

    if kill_codex:
        proc_result = cleanup_codex_processes(dry_run=dry_run)
        if proc_result.candidates:
            click.echo("Codex processes:")
            for pid, cmd in proc_result.candidates:
                click.echo(f"  {pid} {cmd}")
        if proc_result.terminated:
            click.echo("Terminated Codex PIDs:")
            for pid in proc_result.terminated:
                click.echo(f"  {pid}")
        errors.extend(proc_result.errors)

    if codex_temp:
        result = cleanup_codex_temp(dry_run=dry_run)
        removed.extend(result.removed)
        errors.extend(result.errors)

    if worktrees:
        result = cleanup_worktrees(repo_root=repo_root, dry_run=dry_run)
        removed.extend(result.removed)
        errors.extend(result.errors)

    if logs:
        result = cleanup_logs(repo_root=repo_root, dry_run=dry_run)
        removed.extend(result.removed)
        errors.extend(result.errors)

    if codex_home:
        result = cleanup_codex_home(repo_root=repo_root, dry_run=dry_run)
        removed.extend(result.removed)
        errors.extend(result.errors)

    if removed:
        click.echo("\nRemoved:")
        for path in removed:
            click.echo(f"  {path}")
    else:
        click.echo("\nNo paths removed")

    if errors:
        click.echo("\nErrors:")
        for error in errors:
            click.echo(f"  {error}")


if __name__ == "__main__":
    cli()
