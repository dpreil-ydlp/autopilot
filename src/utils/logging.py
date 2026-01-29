"""Structured logging configuration."""

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
from logging.handlers import RotatingFileHandler


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


def _cleanup_old_logs(log_dir: Path, retention_days: int) -> None:
    """Remove log files older than retention_days."""
    if retention_days <= 0:
        return
    cutoff = datetime.now().timestamp() - (retention_days * 86400)
    try:
        for path in log_dir.glob("*.log*"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except FileNotFoundError:
                continue
    except Exception:
        # Best-effort cleanup only.
        return


def setup_logging(
    level: str = "INFO",
    log_file: Optional[Path] = None,
    log_dir: Optional[Path] = None,
    rotation_mb: int = 10,
    retention_days: int = 7,
    use_colors: bool = True,
    console: bool = True,
) -> None:
    """Setup logging configuration.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Optional log file path
        log_dir: Optional log directory (used if log_file not provided)
        rotation_mb: Max log size before rotation (MB)
        retention_days: Days to retain log files (<=0 disables cleanup)
        use_colors: Whether to use colors in console output
        console: Whether to log to console
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper()))

    # Clear existing handlers
    root_logger.handlers.clear()

    # Console handler (optional)
    if console:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(AutopilotFormatter(use_colors=use_colors))
        root_logger.addHandler(console_handler)

    # File handler (if specified)
    if log_file or log_dir:
        if log_file is None:
            if log_dir is None:
                log_dir = Path(".autopilot/logs")
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_file = Path(log_dir) / f"autopilot_{timestamp}.log"
        log_file = Path(log_file)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        _cleanup_old_logs(log_file.parent, retention_days)
        max_bytes = max(1, rotation_mb) * 1024 * 1024
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=max(1, retention_days),
        )
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
