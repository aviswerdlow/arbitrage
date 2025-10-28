"""Data models and helpers for managing matched binary markets."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from arbitrage.events.models import MarketReference


class MarketWindow(BaseModel):
    """Represents the time window in which a market is valid."""

    open_time: datetime
    close_time: datetime
    resolution_time: datetime

    @property
    def is_live(self) -> bool:
        """Return True if the market is currently within the trading window."""

        now = datetime.utcnow()
        return self.open_time <= now <= self.close_time


class MarketPair(BaseModel):
    """Validated equivalence relationship between two binary markets."""

    primary: MarketReference
    hedge: MarketReference
    window: MarketWindow
    llm_similarity: float = Field(..., ge=0.0, le=1.0)
    hard_rules_passed: bool = True
    validator_version: str = "v0"
    last_validated: datetime
    notes: Optional[str] = None

    @property
    def is_tradeable(self) -> bool:
        """Return True if the pair is validated and both markets are live."""

        return self.hard_rules_passed and self.window.is_live


__all__ = ["MarketPair", "MarketWindow"]
