"""Order intent models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from .markets import MarketPair, Venue


class OrderSide(str, Enum):
    """Order side enumeration."""

    BUY = "buy"
    SELL = "sell"


class OrderIntent(BaseModel):
    """Represents a taker order we intend to place."""

    venue: Venue
    market_id: str
    side: OrderSide
    price: float = Field(..., ge=0, le=1)
    size: float = Field(..., gt=0)
    max_slippage: float = Field(..., ge=0)
    created_at: datetime


class HedgeIntent(BaseModel):
    """Represents the paired hedge order and associated metadata."""

    primary: OrderIntent
    hedge: OrderIntent
    expected_edge_cents: float
    hedge_probability: float = Field(..., ge=0, le=1)
    expires_at: Optional[datetime] = None
    market_pair: Optional[MarketPair] = None
