from src.validation.runner import ValidationRunner


def test_rewrite_module_entrypoint_rewrites_tool_to_python_module() -> None:
    rewritten = ValidationRunner._rewrite_module_entrypoint(
        "pytest -q",
        module="pytest",
        python_executable="/tmp/venv/bin/python",
    )
    assert rewritten.startswith("/tmp/venv/bin/python")
    assert " -m pytest " in f" {rewritten} "


def test_rewrite_module_entrypoint_preserves_env_assignments() -> None:
    rewritten = ValidationRunner._rewrite_module_entrypoint(
        "FOO=bar pytest -q",
        module="pytest",
        python_executable="/tmp/venv/bin/python",
    )
    assert rewritten.split()[0] == "FOO=bar"
    assert "/tmp/venv/bin/python" in rewritten


def test_rewrite_module_entrypoint_rewrites_python_dash_m_invocation() -> None:
    rewritten = ValidationRunner._rewrite_module_entrypoint(
        "python3 -m ruff format .",
        module="ruff",
        python_executable="/tmp/venv/bin/python",
    )
    assert rewritten.startswith("/tmp/venv/bin/python")
    assert " -m ruff " in f" {rewritten} "


def test_rewrite_module_entrypoint_noop_for_other_commands() -> None:
    original = "npm test"
    rewritten = ValidationRunner._rewrite_module_entrypoint(
        original,
        module="pytest",
        python_executable="/tmp/venv/bin/python",
    )
    assert rewritten == original


def test_command_invokes_tool_detects_tool_in_shell_chain() -> None:
    assert ValidationRunner._command_invokes_tool("cd backend && ruff check .", tool="ruff")


def test_command_invokes_tool_detects_python_dash_m_in_shell_chain() -> None:
    assert ValidationRunner._command_invokes_tool("cd backend && python -m pytest -q", tool="pytest")


def test_extract_uat_paths_from_ruff_output_extracts_paths() -> None:
    output = """F401 [*] `pytest` imported but unused
 --> tests/uat/test_task_5_uat.py:1:8
  |
1 | import pytest
  |        ^^^^^^
  |
help: Remove unused import: `pytest`
"""
    assert ValidationRunner._extract_uat_paths_from_ruff_output(output) == [
        "tests/uat/test_task_5_uat.py"
    ]


def test_normalize_ruff_lint_command_inserts_check_for_paths() -> None:
    normalized = ValidationRunner._normalize_ruff_lint_command(
        "ruff src/autopilot_smoke/math_add.py tests/autopilot_smoke/test_math_add.py"
    )
    assert normalized.startswith("ruff check ")


def test_normalize_ruff_lint_command_inserts_check_before_fix_flag() -> None:
    normalized = ValidationRunner._normalize_ruff_lint_command("ruff --fix src/foo.py")
    assert normalized == "ruff check --fix src/foo.py"


def test_normalize_ruff_lint_command_inserts_check_for_python_dash_m() -> None:
    normalized = ValidationRunner._normalize_ruff_lint_command("python -m ruff src/foo.py")
    assert normalized == "python -m ruff check src/foo.py"


def test_normalize_ruff_lint_command_noop_when_subcommand_present() -> None:
    original = "cd backend && ruff format ."
    assert ValidationRunner._normalize_ruff_lint_command(original) == original


def test_normalize_ruff_lint_command_noop_for_help_and_version() -> None:
    assert ValidationRunner._normalize_ruff_lint_command("ruff --version") == "ruff --version"
    assert ValidationRunner._normalize_ruff_lint_command("python -m ruff --help") == "python -m ruff --help"


def test_normalize_ruff_lint_command_expands_bare_ruff_to_check_dot() -> None:
    assert ValidationRunner._normalize_ruff_lint_command("ruff") == "ruff check ."
    assert ValidationRunner._normalize_ruff_lint_command("python -m ruff") == "python -m ruff check ."
