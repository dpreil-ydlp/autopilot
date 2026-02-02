#!/usr/bin/env python3
"""Debug subprocess to see what's happening."""

import asyncio
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.utils.subprocess import SubprocessManager


async def test_with_logging():
    """Test with detailed logging."""
    prompt = Path("/Users/davidpreil/Projects/business_finder/plan.md").read_text()[:3000]
    full_prompt = f"Convert this plan to JSON:\n\n{prompt}\n\nOutput ONLY JSON."

    print(f"Prompt length: {len(full_prompt)} characters")

    manager = SubprocessManager(timeout_sec=30, stuck_no_output_sec=10)

    import tempfile
    work_dir = Path(tempfile.gettempdir())

    # Add a custom output handler to log every line
    lines_received = []

    def log_line(line: str):
        lines_received.append(line)
        print(f"LINE ({len(line)} chars): {line[:100]!r}...")

    result = await manager.run(
        command=[
            "claude",
            "--permission-mode", "dontAsk",
            "--print",
            "--verbose",
            "--output-format", "stream-json",
        ],
        cwd=work_dir,
        stdin=full_prompt,
        on_output_line=log_line,
    )

    print(f"\nSuccess: {result['success']}")
    print(f"Exit code: {result.get('exit_code')}")
    print(f"Total lines received: {len(lines_received)}")
    print(f"Output length: {len(result['output'])} characters")


if __name__ == "__main__":
    asyncio.run(test_with_logging())
