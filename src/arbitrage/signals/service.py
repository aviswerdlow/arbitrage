"""Signal computation utilities for mispricing detection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from arbitrage.events.models import EdgeComputation
from arbitrage.markets.pairs import MarketPair


class FrictionModel(Protocol):
    """Calculates expected frictions applied to a trade package."""

    def total_cost_cents(self, pair: MarketPair, size: float) -> float:
        ...


class DepthModel(Protocol):
    """Estimates achievable size and slippage given an order book."""

    def expected_slippage_cents(self, pair: MarketPair, size: float) -> float:
        ...


@dataclass(slots=True)
class SignalRequest:
    """Inputs for the signal engine."""

    pair: MarketPair
    target_size: float
    primary_price: float
    hedge_price: float


@dataclass(slots=True)
class SignalService:
    """Combines depth and friction models to estimate actionable edges."""

    friction_model: FrictionModel
    depth_model: DepthModel
    min_edge_cents: float = 2.5
    min_hedge_probability: float = 0.99

    def compute(self, request: SignalRequest) -> EdgeComputation | None:
        """Return an edge if net opportunity exceeds configured thresholds."""

        gross_edge = (request.hedge_price - request.primary_price) * 100
        friction_cost = self.friction_model.total_cost_cents(request.pair, request.target_size)
        slippage = self.depth_model.expected_slippage_cents(request.pair, request.target_size)
        net_edge = gross_edge - friction_cost - slippage

        if net_edge < self.min_edge_cents:
            return None

        return EdgeComputation(
            primary=request.pair.primary,
            hedge=request.pair.hedge,
            timestamp=request.pair.last_validated,
            net_edge_cents=net_edge,
            expected_slippage_cents=slippage,
            confidence=0.85,
            recommended_primary_side="buy" if net_edge > 0 else "sell",
        )


__all__ = ["DepthModel", "FrictionModel", "SignalRequest", "SignalService"]
