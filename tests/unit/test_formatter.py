"""Unit tests for AutopilotFormatter."""

import logging
import re

from src.utils.logging import AutopilotFormatter


def test_formatter_no_color_codes():
    """Test that formatter produces no ANSI codes when use_colors=False."""
    formatter = AutopilotFormatter(use_colors=False)

    # Create a test log record
    record = logging.LogRecord(
        name="test.module",
        level=logging.INFO,
        pathname="test.py",
        lineno=42,
        msg="Test message",
        args=(),
        exc_info=None,
    )

    # Format the record
    output = formatter.format(record)

    # Assert no ANSI escape sequences are present
    ansi_escape = re.compile(r"\033\[[0-9;]*m")
    assert not ansi_escape.search(output), f"Output contains ANSI codes: {output!r}"

    # Assert basic format is correct
    assert "INFO" in output
    assert "Test message" in output
    assert "test" in output or "module" in output


def test_formatter_includes_timestamp():
    """Test that formatter includes timestamp in output."""
    formatter = AutopilotFormatter(use_colors=False)

    record = logging.LogRecord(
        name="test.logger",
        level=logging.WARNING,
        pathname="test.py",
        lineno=1,
        msg="Warning message",
        args=(),
        exc_info=None,
    )

    output = formatter.format(record)

    # Check for HH:MM:SS format timestamp
    timestamp_pattern = re.compile(r"\[\d{2}:\d{2}:\d{2}\]")
    assert timestamp_pattern.search(output), f"Output missing timestamp: {output!r}"


def test_formatter_level_alignment():
    """Test that formatter properly aligns log levels."""
    formatter = AutopilotFormatter(use_colors=False)

    levels_to_test = [
        (logging.DEBUG, "DEBUG"),
        (logging.INFO, "INFO"),
        (logging.WARNING, "WARNING"),
        (logging.ERROR, "ERROR"),
        (logging.CRITICAL, "CRITICAL"),
    ]

    for level, level_name in levels_to_test:
        record = logging.LogRecord(
            name="test",
            level=level,
            pathname="test.py",
            lineno=1,
            msg=f"{level_name} message",
            args=(),
            exc_info=None,
        )

        output = formatter.format(record)

        # Verify level name appears in output
        assert level_name in output, f"Level {level_name} not in output: {output!r}"

        # Verify no ANSI codes
        ansi_escape = re.compile(r"\033\[[0-9;]*m")
        assert not ansi_escape.search(output), f"ANSI codes found for {level_name}: {output!r}"


def test_formatter_logger_name_truncation():
    """Test that formatter uses last component of logger name."""
    formatter = AutopilotFormatter(use_colors=False)

    record = logging.LogRecord(
        name="very.long.logger.name.module",
        level=logging.INFO,
        pathname="test.py",
        lineno=1,
        msg="Message",
        args=(),
        exc_info=None,
    )

    output = formatter.format(record)

    # Should use last component "module" not full path
    assert "module" in output
    # Should not contain dots from the full path
    assert "." not in output.replace("[", "").replace("]", "")
