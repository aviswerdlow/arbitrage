"""Structured logging helpers aligning with the observability requirements."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping


@dataclass(slots=True)
class EventLogger:
    """Emit JSON-formatted events for downstream ingestion."""

    logger: logging.Logger

    def emit(self, event_type: str, payload: Mapping[str, Any]) -> None:
        """Serialize and log an event with consistent metadata."""

        enriched = {
            "timestamp": datetime.utcnow().isoformat(timespec="milliseconds") + "Z",
            "event_type": event_type,
            "payload": payload,
        }
        self.logger.info(json.dumps(enriched, sort_keys=True))


def configure_logging(level: int = logging.INFO) -> logging.Logger:
    """Configure root logger with structured output."""

    logging.basicConfig(level=level, format="%(message)s")
    return logging.getLogger("arbitrage")


__all__ = ["EventLogger", "configure_logging"]
