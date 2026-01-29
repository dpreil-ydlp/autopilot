#!/usr/bin/env python3
"""Verification script for Autopilot installation."""

import sys
from pathlib import Path


def check_file(path: str, description: str) -> bool:
    """Check if file exists."""
    p = Path(path)
    exists = p.exists()
    status = "✅" if exists else "❌"
    print(f"{status} {description}: {path}")
    return exists


def check_import(module: str, description: str) -> bool:
    """Check if module can be imported."""
    try:
        __import__(module)
        print(f"✅ {description}: {module}")
        return True
    except ImportError as e:
        print(f"❌ {description}: {module} - {e}")
        return False


def main():
    """Run all checks."""
    print("=" * 60)
    print("Autopilot Installation Verification")
    print("=" * 60)
    print()

    all_passed = True

    # Check core files
    print("Core Files:")
    all_passed &= check_file("pyproject.toml", "Project config")
    all_passed &= check_file("README.md", "Documentation")
    all_passed &= check_file(".autopilot/config.yml", "Configuration")
    print()

    # Check source files
    print("Source Files:")
    all_passed &= check_file("src/main.py", "CLI entrypoint")
    all_passed &= check_file("src/config/models.py", "Config models")
    all_passed &= check_file("src/config/loader.py", "Config loader")
    all_passed &= check_file("src/state/persistence.py", "State models")
    all_passed &= check_file("src/state/machine.py", "Orchestrator state machine")
    all_passed &= check_file("src/state/worker.py", "Worker state machine")
    all_passed &= check_file("src/agents/base.py", "Base agent interface")
    all_passed &= check_file("src/agents/claude.py", "Claude Code wrapper")
    all_passed &= check_file("src/agents/codex.py", "Codex/OpenAI wrapper")
    all_passed &= check_file("src/utils/git.py", "Git operations")
    all_passed &= check_file("src/utils/subprocess.py", "Subprocess manager")
    all_passed &= check_file("src/utils/logging.py", "Logging utilities")
    print()

    # Check templates and schemas
    print("Templates & Schemas:")
    all_passed &= check_file("templates/task.md", "Task template")
    all_passed &= check_file("templates/plan.md", "Plan template")
    all_passed &= check_file("schemas/review.json", "Review schema")
    all_passed &= check_file("schemas/plan.json", "Plan schema")
    print()

    # Check module imports
    print("Module Imports:")
    all_passed &= check_import("src.config.models", "Config models")
    all_passed &= check_import("src.state.persistence", "State persistence")
    all_passed &= check_import("src.state.machine", "State machine")
    all_passed &= check_import("src.agents.base", "Agent base")
    all_passed &= check_import("src.agents.claude", "Claude agent")
    all_passed &= check_import("src.agents.codex", "Codex agent")
    all_passed &= check_import("src.utils.git", "Git utils")
    all_passed &= check_import("src.utils.subprocess", "Subprocess utils")
    print()

    # Check CLI
    print("CLI Commands:")
    try:
        import subprocess

        result = subprocess.run(
            ["autopilot", "--help"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            print("✅ autopilot CLI installed")
            print("   Available commands:")
            for line in result.stdout.split("\n"):
                if "  " * 2 in line and not line.strip().startswith("-"):
                    cmd = line.strip().split()[0]
                    if cmd:
                        print(f"     - {cmd}")
        else:
            print("❌ autopilot CLI not working")
            all_passed = False
    except Exception as e:
        print(f"❌ autopilot CLI error: {e}")
        all_passed = False
    print()

    # Summary
    print("=" * 60)
    if all_passed:
        print("✅ All checks passed! Autopilot is ready to use.")
        print()
        print("Next steps:")
        print("  1. Review .autopilot/config.yml")
        print("  2. Create a task file: cp templates/task.md tasks/my-task.md")
        print("  3. Run: autopilot run tasks/my-task.md")
    else:
        print("❌ Some checks failed. Please review the output above.")
        sys.exit(1)
    print("=" * 60)


if __name__ == "__main__":
    main()
