# Plan: Logging Formatter Test Coverage

## Overview
Add small unit tests covering the logging formatter and setup helpers to validate the end-to-end planning, build, review, and UAT loop without changing core behavior.

## Dependencies
None.

## Tasks
1. Add unit tests for `AutopilotFormatter` output format (no color codes when `use_colors=False`).
2. Add unit tests for `setup_logging` to ensure handlers are created and log level is applied.
3. Add unit tests for `get_logger` to ensure it returns the named logger instance.

## Success Criteria
- New tests pass with `pytest -q`.
- No changes to production behavior.

## Notes
- Keep changes within `tests/` unless a test needs a tiny helper in `src/utils/logging.py`.
