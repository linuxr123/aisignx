"""Log file setup: LOG_DIR/aisignx.log, rotated at 5 MB, five old files kept.

Lines look like `2026-11-12T10:00:00Z INFO    aisignx.jobs: message` (UTC). The level
is the second space-separated token, which the System > Logs viewer filters on.
Modules log with logging.getLogger(__name__); everything under "aisignx" (app.logger)
and the "waitress" server logger goes to the file. One process writes the file.
"""
import logging
import os
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import Flask

LOG_NAME = "aisignx.log"
LOG_FORMAT = "%(asctime)sZ %(levelname)-7s %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 5
LOGGER_NAMES = ("aisignx", "waitress")


class _UTCFormatter(logging.Formatter):
    converter = time.gmtime


def log_file_path(app: Flask) -> Path:
    return Path(app.config["LOG_DIR"]) / LOG_NAME


def _level(app: Flask) -> int:
    value = app.config.get("LOG_LEVEL") or "INFO"
    if isinstance(value, int):
        return value
    level = logging.getLevelName(str(value).strip().upper())
    return level if isinstance(level, int) else logging.INFO


def configure_logging(app: Flask) -> RotatingFileHandler | None:
    """Attach the rotating file handler, replacing one added by an earlier create_app().

    Skipped when TESTING unless LOG_TO_FILE is true. Returns the handler, or None.
    """
    loggers = [logging.getLogger(name) for name in LOGGER_NAMES]
    for logger in loggers:
        for handler in [h for h in logger.handlers if getattr(h, "_aisignx", False)]:
            logger.removeHandler(handler)
            handler.close()

    if app.config.get("TESTING") and not app.config.get("LOG_TO_FILE"):
        return None

    path = log_file_path(app)
    try:
        os.makedirs(path.parent, exist_ok=True)
        handler = RotatingFileHandler(path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT,
                                      encoding="utf-8", delay=True)
    except OSError:
        app.logger.exception("Cannot write the log file %s; logging to the console only.",
                             path)
        return None
    handler._aisignx = True  # type: ignore[attr-defined]
    handler.setFormatter(_UTCFormatter(LOG_FORMAT, DATE_FORMAT))
    level = _level(app)
    handler.setLevel(level)
    for logger in loggers:
        logger.addHandler(handler)
        logger.setLevel(level)
    return handler
