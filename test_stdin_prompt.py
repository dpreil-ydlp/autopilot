#!/usr/bin/env python3
"""Test passing prompt via stdin."""

import asyncio
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.utils.subprocess import SubprocessManager


async def test_stdin_prompt():
    """Test passing prompt via stdin."""
    prompt = "Say 'hello from stdin' and nothing else."

    manager = SubprocessManager(timeout_sec=30, stuck_no_output_sec=10)

    import tempfile
    work_dir = Path(tempfile.gettempdir())

    # Use echo to pipe the prompt to claude
    result = await manager.run(
        command=[
            "sh", "-c",
            f'echo "{prompt}" | claude --permission-mode dontAsk --print --verbose --output-format stream-json'
        ],
        cwd=work_dir,
        capture_output=True,
    )

    print(f"Success: {result['success']}")
    print(f"Exit code: {result.get('exit_code')}")
    print(f"Output length: {len(result['output'])} characters")
    print(f"First 300 chars:\n{result['output'][:300]}")


if __name__ == "__main__":
    asyncio.run(test_stdin_prompt())
