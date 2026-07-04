"""Centralized logging helpers."""

from __future__ import annotations

import logging

DEFAULT_LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
VERBOSE_LOG_FORMAT = "%(asctime)s.%(msecs)03d %(name)-30s %(levelname)-5s | %(message)s"
NOISY_LOGGERS = ("httpx", "httpcore", "anthropic", "openai", "asyncio", "urllib3")


def configure_logging(
    *,
    debug: bool = False,
    level: int | None = None,
    force: bool = False,
    verbose: bool = False,
) -> None:
    """Configure root logging from one place."""
    resolved_level = (
        level if level is not None else (logging.DEBUG if debug else logging.WARNING)
    )
    logging.basicConfig(
        level=resolved_level,
        format=VERBOSE_LOG_FORMAT if verbose else DEFAULT_LOG_FORMAT,
        datefmt="%H:%M:%S" if verbose else None,
        force=force,
    )
    for noisy in NOISY_LOGGERS:
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Return a module-specific logger."""
    return logging.getLogger(name)


log = get_logger(__name__)
