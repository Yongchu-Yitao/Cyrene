"""Persistent rotating-file logging shared by all runtime entry points.

Console-only ``basicConfig`` loses the record of a run as soon as the process
exits. This module attaches a time-based rotating file handler to the root
logger (plus quieter levels for chatty third-party HTTP loggers), so a daemon
or Electron run keeps an inspectable trace on disk regardless of terminal
capture. Logs roll every 2 hours and keep a rolling 3-day window (36 files).
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
from pathlib import Path

from cyrene.config import DATA_DIR

# Per-request INFO logs from these libraries drown out first-party events.
_LOUD_THIRD_PARTY_LOGGERS = ("httpx", "httpcore", "urllib3", "openai", "aiohttp")

# Rolling window: one file per 2 hours, 3 days of history retained.
_ROLLOVER_INTERVAL_HOURS = 2
_ROLLOVER_BACKUP_COUNT = 36  # 36 * 2h = 72h = 3 days


def setup_persistent_logging(
    log_dir: Path | None = None,
    *,
    rollover_interval_hours: int = _ROLLOVER_INTERVAL_HOURS,
    backup_count: int = _ROLLOVER_BACKUP_COUNT,
) -> Path | None:
    """Attach a 2-hourly rotating file handler to the root logger; idempotent.

    Returns the log file path, or None when file logging could not be set up
    (console-only logging then remains in effect).
    """
    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(handler, TimedRotatingFileHandler):
            return Path(handler.baseFilename)

    log_dir = log_dir or (DATA_DIR / "logs")
    log_file = log_dir / "cyrene.log"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = TimedRotatingFileHandler(
            log_file,
            when="H",
            interval=rollover_interval_hours,
            backupCount=backup_count,
            encoding="utf-8",
        )
    except OSError:
        logging.getLogger(__name__).warning(
            "File logging unavailable at %s; keeping console-only logging",
            log_file,
            exc_info=True,
        )
        return None
    handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )
    root.addHandler(handler)
    if root.level >= logging.WARNING:
        # Default root level is WARNING; entry points that already ran
        # basicConfig(level=INFO) keep their explicit level untouched.
        root.setLevel(logging.INFO)

    for name in _LOUD_THIRD_PARTY_LOGGERS:
        third_party = logging.getLogger(name)
        if third_party.level == logging.NOTSET:
            third_party.setLevel(logging.WARNING)

    return log_file
