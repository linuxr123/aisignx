import logging
import os
from pathlib import Path

# Setup log directory and file
LOG_DIR = Path("logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "digital_signage.log"

logger = logging.getLogger("digital_signage")
logger.setLevel(logging.INFO)

def configure_logging():
    try:
        from concurrent_log_handler import ConcurrentRotatingFileHandler as Rotating
    except Exception:
        from logging.handlers import RotatingFileHandler as Rotating

    if any(isinstance(h, logging.FileHandler) for h in logger.handlers):
        return

    try:
        file_handler = Rotating(
            LOG_FILE,
            maxBytes=5 * 1024 * 1024,  # 5 MB
            backupCount=5,
            encoding="utf-8",
            delay=True,
        )
    except TypeError:
        file_handler = Rotating(
            str(LOG_FILE),
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
        )

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]"
    )
    file_handler.setFormatter(fmt)
    file_handler.setLevel(logging.INFO)
    logger.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.setLevel(logging.INFO)
    logger.addHandler(console)