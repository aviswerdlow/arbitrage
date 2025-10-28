"""Risk management utilities enforcing portfolio and venue limits."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from arbitrage.events.models import ExecutionIntent


class RiskStore(Protocol):
    """Persists and retrieves exposure metrics."""

    def total_notional(self, venue: str) -> float:
        ...

    def increment_notional(self, venue: str, amount: float) -> None:
        ...


@dataclass(slots=True)
class RiskLimits:
    """Hard limits derived from the PRD."""

    venue_cap: float = 5_000.0
    per_contract_limit: float = 250.0
    concurrent_pairs: int = 5


@dataclass(slots=True)
class RiskManager:
    """Enforces risk limits before intents reach the execution engine."""

    store: RiskStore
    limits: RiskLimits = RiskLimits()

    def approve(self, intent: ExecutionIntent) -> bool:
        """Return True when the intent stays within limits."""

        venue = intent.edge.primary.venue
        current = self.store.total_notional(venue)
        if current + intent.max_notional > self.limits.venue_cap:
            return False
        if intent.max_notional > self.limits.per_contract_limit:
            return False
        self.store.increment_notional(venue, intent.max_notional)
        return True


__all__ = ["RiskLimits", "RiskManager", "RiskStore"]
