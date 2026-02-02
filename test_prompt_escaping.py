#!/usr/bin/env python3
"""Test if prompt escaping is causing issues."""

import subprocess
import tempfile
from pathlib import Path

# Read the plan
plan_path = Path("/Users/davidpreil/Projects/business_finder/plan.md")
plan_content = plan_path.read_text()

# Create a simple prompt like autopilot does
prompt = f"""Convert the following plan into a task dependency graph with enriched metadata.

## Plan
{plan_content[:3000]}

Return JSON with the key:
- tasks: list of task objects

Output ONLY the JSON, no other text."""

print(f"Prompt length: {len(prompt)} characters")
print(f"Prompt preview (first 200 chars):\n{prompt[:200]}")

# Check for special characters that might cause issues
special_chars = []
for i, char in enumerate(prompt):
    if ord(char) < 32 and char not in '\n\r\t':
        special_chars.append((i, char, ord(char)))

if special_chars:
    print(f"\nFound {len(special_chars)} control characters:")
    for pos, char, code in special_chars[:10]:
        print(f"  Position {pos}: {repr(char)} (code {code})")
else:
    print("\nNo problematic control characters found")

# Try running with the prompt
try:
    result = subprocess.run(
        ["claude", "--permission-mode", "dontAsk", "--print", "--verbose",
         "--output-format", "stream-json", prompt],
        capture_output=True,
        text=True,
        timeout=10,
        cwd=tempfile.gettempdir()
    )
    print(f"\n✓ Command completed successfully")
    print(f"Return code: {result.returncode}")
    print(f"Output length: {len(result.stdout)} characters")
    print(f"Stderr length: {len(result.stderr)} characters")
except subprocess.TimeoutExpired:
    print("\n✗ Command timed out after 10 seconds")
    print("This suggests the CLI is hanging on this prompt")
except Exception as e:
    print(f"\n✗ Error: {e}")
