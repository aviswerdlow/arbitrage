"""Structured logging configuration for the platform."""

from __future__ import annotations

import logging

import structlog

from arbitrage.config import get_settings


def _get_shared_processors() -> list[structlog.types.Processor]:
    """Common structlog processors."""

    return [
        structlog.contextvars.merge_contextvars,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]


def configure_logging() -> None:
    """Configure structlog and standard logging."""

    settings = get_settings()
    shared_processors = _get_shared_processors()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(message)s",
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return configured logger."""

    return structlog.get_logger(name)
