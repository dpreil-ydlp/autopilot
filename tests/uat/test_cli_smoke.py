from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from src.main import cli


def test_cli_init_creates_config(tmp_path: Path) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        config_path = Path(".autopilot") / "config.yml"
        result = runner.invoke(cli, ["--config", str(config_path), "init"])
        assert result.exit_code == 0, result.output
        assert config_path.exists()


def test_cli_status_no_run(tmp_path: Path) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        config_path = Path(".autopilot") / "config.yml"
        # Initialize config first
        init_result = runner.invoke(cli, ["--config", str(config_path), "init"])
        assert init_result.exit_code == 0, init_result.output

        # Status should indicate no run in progress
        status_result = runner.invoke(cli, ["--config", str(config_path), "status"])
        assert status_result.exit_code == 0
        assert "No Autopilot run in progress" in status_result.output


def test_cli_run_requires_target(tmp_path: Path) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        config_path = Path(".autopilot") / "config.yml"
        init_result = runner.invoke(cli, ["--config", str(config_path), "init"])
        assert init_result.exit_code == 0, init_result.output

        run_result = runner.invoke(cli, ["--config", str(config_path), "run"])
        assert run_result.exit_code != 0
        assert "No task or plan specified" in run_result.output
