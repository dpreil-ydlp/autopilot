"""Unit tests for logging configuration."""

import logging
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


class TestSetupLogging:
    """Tests for setup_logging function."""

    def test_console_handler_created(self):
        """Test console handler is always created."""
        setup_logging()

        root = logging.getLogger()
        # Check that AutopilotFormatter is present (our custom handler)
        formatters = [
            h.formatter for h in root.handlers if isinstance(h.formatter, AutopilotFormatter)
        ]

        assert len(formatters) >= 1
        assert isinstance(formatters[0], AutopilotFormatter)

    def test_console_and_file_handler_created(self, tmp_path):
        """Test both console and file handlers when log_file is provided."""
        log_file = tmp_path / "test.log"
        setup_logging(log_file=log_file)

        root = logging.getLogger()
        stream_handlers = [h for h in root.handlers if isinstance(h, logging.StreamHandler)]
        file_handlers = [h for h in root.handlers if isinstance(h, logging.FileHandler)]

        assert len(stream_handlers) >= 1
        assert len(file_handlers) == 1
        assert file_handlers[0].baseFilename == str(log_file)

    def test_log_level_debug(self):
        """Test DEBUG level configuration."""
        setup_logging(level="DEBUG")

        root = logging.getLogger()
        assert root.level == logging.DEBUG

    def test_log_level_info(self):
        """Test INFO level configuration (default)."""
        setup_logging(level="INFO")

        root = logging.getLogger()
        assert root.level == logging.INFO

    def test_log_level_warning(self):
        """Test WARNING level configuration."""
        setup_logging(level="WARNING")

        root = logging.getLogger()
        assert root.level == logging.WARNING

    def test_log_level_error(self):
        """Test ERROR level configuration."""
        setup_logging(level="ERROR")

        root = logging.getLogger()
        assert root.level == logging.ERROR

    def test_log_level_critical(self):
        """Test CRITICAL level configuration."""
        setup_logging(level="CRITICAL")

        root = logging.getLogger()
        assert root.level == logging.CRITICAL

    def test_log_level_case_insensitive(self):
        """Test log level is case-insensitive."""
        setup_logging(level="debug")
        assert logging.getLogger().level == logging.DEBUG

        setup_logging(level="Info")
        assert logging.getLogger().level == logging.INFO

        setup_logging(level="WaRnInG")
        assert logging.getLogger().level == logging.WARNING

    def test_existing_handlers_cleared(self):
        """Test existing handlers are cleared before setup."""
        root = logging.getLogger()

        # Clear pytest's handlers first
        original_handler_count = len(root.handlers)

        existing_handler = logging.StreamHandler()
        root.addHandler(existing_handler)

        assert len(root.handlers) == original_handler_count + 1

        setup_logging()

        # Our handler should be removed (pytest's handlers remain)
        assert existing_handler not in root.handlers

    def test_console_handler_colors_enabled(self):
        """Test console handler uses colored formatter by default."""
        setup_logging(use_colors=True)

        root = logging.getLogger()
        stream_handlers = [h for h in root.handlers if isinstance(h, logging.StreamHandler)]

        # Find our handler (not pytest's)
        autopilot_handlers = [
            h for h in stream_handlers if isinstance(h.formatter, AutopilotFormatter)
        ]

        assert len(autopilot_handlers) >= 1
        assert autopilot_handlers[0].formatter.use_colors is True

    def test_console_handler_colors_disabled(self):
        """Test console handler can disable colors."""
        setup_logging(use_colors=False)

        root = logging.getLogger()
        stream_handlers = [h for h in root.handlers if isinstance(h, logging.StreamHandler)]

        # Find our handler (not pytest's)
        autopilot_handlers = [
            h for h in stream_handlers if isinstance(h.formatter, AutopilotFormatter)
        ]

        assert len(autopilot_handlers) >= 1
        assert autopilot_handlers[0].formatter.use_colors is False

    def test_file_handler_no_colors(self, tmp_path):
        """Test file handler never uses colors."""
        log_file = tmp_path / "test.log"
        setup_logging(log_file=log_file, use_colors=True)

        root = logging.getLogger()
        file_handlers = [h for h in root.handlers if isinstance(h, logging.FileHandler)]

        assert len(file_handlers) == 1
        assert isinstance(file_handlers[0].formatter, AutopilotFormatter)
        assert file_handlers[0].formatter.use_colors is False

    def test_noisy_loggers_suppressed(self):
        """Test noisy third-party loggers are suppressed."""
        setup_logging()

        httpx_logger = logging.getLogger("httpx")
        httpcore_logger = logging.getLogger("httpcore")
        openai_logger = logging.getLogger("openai")

        assert httpx_logger.level == logging.WARNING
        assert httpcore_logger.level == logging.WARNING
        assert openai_logger.level == logging.WARNING

    def test_log_file_directory_created(self, tmp_path):
        """Test log file parent directories are created."""
        log_file = tmp_path / "nested" / "dir" / "test.log"
        assert not log_file.parent.exists()

        setup_logging(log_file=log_file)

        assert log_file.parent.exists()
        assert log_file.parent.is_dir()

    def test_logger_outputs_to_stderr(self, capsys):
        """Test logger outputs to stderr."""
        setup_logging(level="INFO")
        logger = get_logger("test")

        logger.info("Test message")

        captured = capsys.readouterr()
        assert "Test message" in captured.err

    def test_logger_filters_by_level(self, capsys):
        """Test logger respects level filtering."""
        setup_logging(level="WARNING")
        logger = get_logger("test")

        logger.debug("Debug message")
        logger.info("Info message")
        logger.warning("Warning message")

        captured = capsys.readouterr()
        assert "Debug message" not in captured.err
        assert "Info message" not in captured.err
        assert "Warning message" in captured.err


class TestAutopilotFormatter:
    """Tests for AutopilotFormatter class."""

    def test_formatter_with_colors(self):
        """Test formatter includes color codes."""
        formatter = AutopilotFormatter(use_colors=True)

        record = logging.LogRecord(
            name="test.module",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )

        with patch("sys.stderr.isatty", return_value=True):
            formatted = formatter.format(record)

        assert "\033[32m" in formatted  # Green for INFO
        assert "\033[0m" in formatted  # Reset code
        assert "Test message" in formatted
        assert "INFO" in formatted

    def test_formatter_without_colors(self):
        """Test formatter without colors."""
        formatter = AutopilotFormatter(use_colors=False)

        record = logging.LogRecord(
            name="test.module",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )

        formatted = formatter.format(record)

        assert "\033[" not in formatted  # No ANSI codes
        assert "Test message" in formatted
        assert "INFO" in formatted

    def test_formatter_shortens_logger_name(self):
        """Test formatter uses last component of logger name."""
        formatter = AutopilotFormatter(use_colors=False)

        record = logging.LogRecord(
            name="very.long.logger.name",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test",
            args=(),
            exc_info=None,
        )

        formatted = formatter.format(record)

        assert "name" in formatted  # Last component
        assert "very.long.logger" not in formatted

    def test_formatter_all_levels_have_colors(self):
        """Test all log levels have defined colors."""
        formatter = AutopilotFormatter(use_colors=True)

        levels = [
            (logging.DEBUG, "\033[36m"),  # Cyan
            (logging.INFO, "\033[32m"),  # Green
            (logging.WARNING, "\033[33m"),  # Yellow
            (logging.ERROR, "\033[31m"),  # Red
            (logging.CRITICAL, "\033[35m"),  # Magenta
        ]

        with patch("sys.stderr.isatty", return_value=True):
            for level, color_code in levels:
                record = logging.LogRecord(
                    name="test",
                    level=level,
                    pathname="test.py",
                    lineno=1,
                    msg="Test",
                    args=(),
                    exc_info=None,
                )

                formatted = formatter.format(record)
                assert color_code in formatted, f"Missing color for {logging.getLevelName(level)}"


class TestGetLogger:
    """Tests for get_logger function."""

    def test_get_logger_returns_logger(self):
        """Test get_logger returns a Logger instance."""
        logger = get_logger("test")
        assert isinstance(logger, logging.Logger)
        assert logger.name == "test"

    def test_get_logger_same_name_same_instance(self):
        """Test get_logger returns same instance for same name."""
        logger1 = get_logger("test")
        logger2 = get_logger("test")
        assert logger1 is logger2

    def test_get_logger_different_names_different_instances(self):
        """Test get_logger returns different instances for different names."""
        logger1 = get_logger("test1")
        logger2 = get_logger("test2")
        assert logger1 is not logger2
        assert logger1.name == "test1"
        assert logger2.name == "test2"
