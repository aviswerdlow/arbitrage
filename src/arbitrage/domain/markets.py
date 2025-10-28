"""Market and venue models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Venue(str, Enum):
    """Supported trading venues."""

    POLYMARKET = "polymarket"
    KALSHI = "kalshi"


class Market(BaseModel):
    """Canonical representation of a binary market."""

    id: str = Field(..., description="Internal market identifier")
    venue: Venue
    venue_market_id: str = Field(..., description="Venue-specific market identifier")
    event_name: str
    contract_name: str
    open_time: datetime
    close_time: datetime
    resolution_source: str
    unit: str = Field(default="USD")
    tags: list[str] = Field(default_factory=list)


class MarketPair(BaseModel):
    """Validated equivalent markets across venues."""

    id: str
    primary_market: Market
    hedge_market: Market
    validation_score: float = Field(..., ge=0, le=1)
    validated_at: datetime
    validator_notes: Optional[str] = None
