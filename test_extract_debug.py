#!/usr/bin/env python3
"""Test _extract_summary with debug output."""

import asyncio
import sys
import logging
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

# Enable debug logging
logging.basicConfig(level=logging.INFO)

from src.agents.claude import ClaudeAgent


async def test_extract():
    """Test _extract_summary with actual chunk content."""
    chunk_path = Path("/Users/davidpreil/Projects/business_finder/.autopilot/plan/chunks/chunk_1.md")
    if not chunk_path.exists():
        print(f"Chunk file not found: {chunk_path}")
        return

    chunk_content = chunk_path.read_text()
    print(f"Chunk content length: {len(chunk_content)} characters")

    # Create prompt like autopilot does
    prompt = f"""Convert the following plan into a task dependency graph with enriched metadata.

## Plan
{chunk_content}

Return JSON with the key:
- tasks: list of task objects

Output ONLY the JSON, no other text."""

    # Run Claude agent
    agent = ClaudeAgent({"cli_path": "claude", "permission_mode": "dontAsk"})

    import tempfile
    work_dir = Path(tempfile.gettempdir())

    try:
        result = await agent.plan(
            plan_content=chunk_content,
            timeout_sec=30,
            work_dir=work_dir,
            context="Test chunk",
        )
        print(f"\n✓ Plan extraction successful!")
        print(f"Result keys: {list(result.keys()) if isinstance(result, dict) else type(result)}")
        if isinstance(result, dict) and 'tasks' in result:
            print(f"Number of tasks: {len(result['tasks'])}")
    except Exception as e:
        print(f"\n✗ Plan extraction failed: {e}")


if __name__ == "__main__":
    asyncio.run(test_extract())
