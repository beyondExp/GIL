from __future__ import annotations

import logging
import sys

from gil.core.config import GilSettings

_CONFIGURED = False


def configure_logging(settings: GilSettings | None = None) -> logging.Logger:
    """Single structured logger for the GIL core. Consumes GIL_LOG_LEVEL."""
    global _CONFIGURED
    s = settings or GilSettings()
    level_name = str(s.log_level or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logger = logging.getLogger("gil")
    logger.setLevel(level)
    if not _CONFIGURED:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s gil.%(name)s %(message)s")
        )
        logger.addHandler(handler)
        logger.propagate = False
        _CONFIGURED = True
    return logger


def get_logger(name: str = "gil") -> logging.Logger:
    if not _CONFIGURED:
        configure_logging()
    return logging.getLogger(name if name.startswith("gil") else f"gil.{name}")
