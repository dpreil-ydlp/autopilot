"""Unit tests for get_logger function."""

import logging

from src.utils.logging import get_logger


class TestGetLogger:
    """Tests for get_logger function."""

    def test_get_logger_returns_named_logger(self):
        """Test get_logger returns a logger with the correct name."""
        logger = get_logger("test_module")
        assert isinstance(logger, logging.Logger)
        assert logger.name == "test_module"

    def test_get_logger_with_simple_name(self):
        """Test get_logger works with simple single-part names."""
        logger = get_logger("simple")
        assert logger.name == "simple"
        assert isinstance(logger, logging.Logger)

    def test_get_logger_with_dotted_name(self):
        """Test get_logger works with dotted module paths."""
        logger = get_logger("package.module.submodule")
        assert logger.name == "package.module.submodule"
        assert isinstance(logger, logging.Logger)

    def test_get_logger_same_name_same_instance(self):
        """Test repeated calls return the same logger instance for the same name."""
        logger1 = get_logger("duplicate_test")
        logger2 = get_logger("duplicate_test")
        assert logger1 is logger2
        assert logger1.name == "duplicate_test"
        assert logger2.name == "duplicate_test"

    def test_get_logger_different_names_different_instances(self):
        """Test get_logger returns different instances for different names."""
        logger1 = get_logger("logger_one")
        logger2 = get_logger("logger_two")
        assert logger1 is not logger2
        assert logger1.name == "logger_one"
        assert logger2.name == "logger_two"

    def test_get_logger_multiple_calls_consistency(self):
        """Test that multiple calls for the same name consistently return the same instance."""
        loggers = [get_logger("consistency_test") for _ in range(5)]
        for logger in loggers[1:]:
            assert logger is loggers[0]
        assert all(logger.name == "consistency_test" for logger in loggers)

    def test_get_logger_empty_name(self):
        """Test get_logger with empty string returns root logger."""
        logger = get_logger("")
        assert isinstance(logger, logging.Logger)
        assert logger.name == "root"  # Python's logging.getLogger("") returns root logger

    def test_get_logger_returns_standard_logger(self):
        """Test get_logger returns standard Python logging.Logger instance."""
        logger = get_logger("standard_test")
        assert hasattr(logger, "debug")
        assert hasattr(logger, "info")
        assert hasattr(logger, "warning")
        assert hasattr(logger, "error")
        assert hasattr(logger, "critical")
