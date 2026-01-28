"""Structured logging configuration."""

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


class AutopilotFormatter(logging.Formatter):
    """Custom formatter with colors and timestamps."""

    # Color codes
    COLORS = {
        "DEBUG": "\033[36m",  # Cyan
        "INFO": "\033[32m",  # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",  # Red
        "CRITICAL": "\033[35m",  # Magenta
        "RESET": "\033[0m",
    }

    def __init__(self, use_colors: bool = True):
        """Initialize formatter.

        Args:
            use_colors: Whether to use colors in output
        """
        super().__init__()
        self.use_colors = use_colors

    def format(self, record: logging.LogRecord) -> str:
        """Format log record.

        Args:
            record: Log record to format

        Returns:
            Formatted log message
        """
        levelname = record.levelname

        if self.use_colors and sys.stderr.isatty():
            color = self.COLORS.get(levelname, "")
            reset = self.COLORS["RESET"]
            colored_level = f"{color}{levelname}{reset}"
        else:
            colored_level = levelname

        timestamp = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        name = record.name.split(".")[-1]  # Use last component of logger name

        message = record.getMessage()

        return f"[{timestamp}] {colored_level:8} {name:12} {message}"


def setup_logging(
    level: str = "INFO",
    log_file: Optional[Path] = None,
    use_colors: bool = True,
) -> None:
    """Setup logging configuration.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Optional log file path
        use_colors: Whether to use colors in console output
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper()))

    # Clear existing handlers
    root_logger.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(AutopilotFormatter(use_colors=use_colors))
    root_logger.addHandler(console_handler)

    # File handler (if specified)
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(AutopilotFormatter(use_colors=False))
        root_logger.addHandler(file_handler)

    # Suppress noisy loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Get logger instance.

    Args:
        name: Logger name

    Returns:
        Logger instance
    """
    return logging.getLogger(name)
