"""UAT tests for logging utilities."""

import logging
import re
from datetime import datetime
from unittest.mock import patch

import pytest

from src.utils.logging import AutopilotFormatter, get_logger, setup_logging


@pytest.fixture(autouse=True)
def reset_logging():
    """Reset logging configuration before and after each test."""
    root = logging.getLogger()
    original_level = root.level
    original_handlers = root.handlers.copy()

    yield

    root.setLevel(original_level)
    root.handlers.clear()
    for handler in original_handlers:
        root.addHandler(handler)


class TestUATAutopilotFormatter:
    """UAT tests for AutopilotFormatter output formatting."""

    def test_no_ansi_and_expected_structure_without_colors(self):
        """Test formatter outputs plain structured text when colors are disabled."""
        formatter = AutopilotFormatter(use_colors=False)
        record = logging.LogRecord(
            name="app.worker",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Processed item",
            args=(),
            exc_info=None,
        )
        record.created = datetime(2024, 1, 2, 3, 4, 5).timestamp()

        with patch("sys.stderr.isatty", return_value=True):
            output = formatter.format(record)

        ansi_escape = re.compile(r"\033\[[0-9;]*m")
        assert not ansi_escape.search(output)
        expected = "[03:04:05] INFO     worker       Processed item"
        assert output == expected

    def test_message_formatting_with_args_no_colors(self):
        """Test formatter renders message arguments without ANSI codes."""
        formatter = AutopilotFormatter(use_colors=False)
        record = logging.LogRecord(
            name="service.api",
            level=logging.WARNING,
            pathname="test.py",
            lineno=10,
            msg="Failed to process %s",
            args=("request",),
            exc_info=None,
        )
        record.created = datetime(2024, 6, 7, 8, 9, 10).timestamp()

        with patch("sys.stderr.isatty", return_value=True):
            output = formatter.format(record)

        assert "Failed to process request" in output
        assert "WARNING" in output
        assert "api" in output
        assert "\033[" not in output


class TestUATSetupLogging:
    """UAT tests for setup_logging handler creation and levels."""

    def test_handlers_and_level_configured(self, tmp_path):
        """Test setup_logging attaches handlers and sets the requested level."""
        log_file = tmp_path / "uat.log"

        setup_logging(level="WARNING", log_file=log_file, use_colors=False)

        root = logging.getLogger()
        stream_handlers = [h for h in root.handlers if isinstance(h, logging.StreamHandler)]
        file_handlers = [h for h in root.handlers if isinstance(h, logging.FileHandler)]

        assert root.level == logging.WARNING
        assert len(stream_handlers) >= 1
        assert len(file_handlers) == 1
        assert file_handlers[0].baseFilename == str(log_file)
        assert isinstance(file_handlers[0].formatter, AutopilotFormatter)
        assert file_handlers[0].formatter.use_colors is False

    def test_invalid_level_raises_attribute_error(self):
        """Test setup_logging raises an error for invalid log levels."""
        with pytest.raises(AttributeError):
            setup_logging(level="NOT_A_LEVEL")

    def test_logging_workflow_no_colors(self, capsys):
        """Test end-to-end logging output contains no ANSI codes when disabled."""
        setup_logging(level="INFO", use_colors=False)
        logger = get_logger("uat.workflow")

        with patch("sys.stderr.isatty", return_value=True):
            logger.info("Workflow message")

        captured = capsys.readouterr()
        assert "Workflow message" in captured.err
        assert "\033[" not in captured.err


class TestUATGetLogger:
    """UAT tests for get_logger named logger behavior."""

    def test_returns_named_logger_and_is_singleton(self):
        """Test get_logger returns consistent named logger instances."""
        logger_one = get_logger("uat.logger")
        logger_two = get_logger("uat.logger")

        assert logger_one.name == "uat.logger"
        assert logger_one is logger_two

    def test_empty_name_returns_root_logger(self):
        """Test get_logger with empty name returns the root logger."""
        logger = get_logger("")
        assert logger is logging.getLogger()
