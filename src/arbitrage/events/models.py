"""Canonical event models propagated across services via Redis streams."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


class EventType(str, Enum):
    """Enumeration describing the lifecycle of trading events."""

    MARKET_SNAPSHOT = "market_snapshot"
    ORDERBOOK_UPDATE = "orderbook_update"
    TRADE = "trade"
    EDGE_COMPUTED = "edge_computed"
    EXECUTION_DECISION = "execution_decision"
    EXECUTION_RESULT = "execution_result"


class MarketReference(BaseModel):
    """Minimal identifier for a market on a venue."""

    venue: str = Field(..., description="Venue slug, e.g. polymarket or kalshi.")
    market_id: str = Field(..., description="Venue-specific market identifier.")
    symbol: str = Field(..., description="Canonicalized market symbol.")


class OrderBookLevel(BaseModel):
    """Simple price level representation for depth calculations."""

    price: float
    size: float


class OrderBookSnapshot(BaseModel):
    """Order book snapshot limited to the top-of-book and first few levels."""

    market: MarketReference
    timestamp: datetime
    bids: list[OrderBookLevel] = Field(default_factory=list)
    asks: list[OrderBookLevel] = Field(default_factory=list)

    class Config:
        json_schema_extra = {
            "example": {
                "market": {
                    "venue": "polymarket",
                    "market_id": "123",
                    "symbol": "us_election_yes",
                },
                "timestamp": "2024-01-01T00:00:00Z",
                "bids": [{"price": 0.45, "size": 120.0}],
                "asks": [{"price": 0.46, "size": 150.0}],
            }
        }


class EdgeComputation(BaseModel):
    """Signal emitted when a mispricing opportunity is detected."""

    primary: MarketReference
    hedge: MarketReference
    timestamp: datetime
    net_edge_cents: float = Field(..., description="Net edge after all frictions in cents.")
    expected_slippage_cents: float = Field(
        ..., description="Expected combined slippage cost in cents for the trade size."
    )
    confidence: float = Field(..., ge=0.0, le=1.0, description="Model confidence score.")
    recommended_primary_side: Literal["buy", "sell"]


class ExecutionIntent(BaseModel):
    """Decision event carrying the desired execution parameters."""

    edge: EdgeComputation
    intent_id: str = Field(..., description="Unique identifier for audit logging.")
    max_notional: float = Field(..., description="Maximum notional allowed for the package.")
    hedge_probability: float = Field(
        ..., ge=0.0, le=1.0, description="Probability that hedge will fill within SLA."
    )


class ExecutionResult(BaseModel):
    """Outcome of an execution attempt, including latency metrics."""

    intent_id: str
    success: bool
    hedge_completed_ms: Optional[int] = None
    message: Optional[str] = None


__all__ = [
    "EdgeComputation",
    "EventType",
    "ExecutionIntent",
    "ExecutionResult",
    "MarketReference",
    "OrderBookLevel",
    "OrderBookSnapshot",
]
