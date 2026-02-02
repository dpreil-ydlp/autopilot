#!/usr/bin/env python3
"""Test subprocess handling with Claude CLI."""

import asyncio
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.utils.subprocess import SubprocessManager


async def test_claude_subprocess():
    """Test that Claude CLI subprocess works correctly."""
    prompt = "Say 'hello world' and nothing else."

    manager = SubprocessManager(timeout_sec=60, stuck_no_output_sec=30)

    import tempfile
    work_dir = Path(tempfile.gettempdir())

    result = await manager.run(
        command=[
            "claude",
            "--permission-mode", "dontAsk",
            "--print",
            "--verbose",
            "--output-format", "stream-json",
            prompt,
        ],
        cwd=work_dir,
        capture_output=True,
    )

    print(f"Success: {result['success']}")
    print(f"Exit code: {result.get('exit_code')}")
    print(f"Timed out: {result.get('timed_out')}")
    print(f"Stuck: {result.get('stuck')}")
    print(f"Output length: {len(result['output'])} characters")
    print(f"First 500 chars:\n{result['output'][:500]}")


if __name__ == "__main__":
    asyncio.run(test_claude_subprocess())
