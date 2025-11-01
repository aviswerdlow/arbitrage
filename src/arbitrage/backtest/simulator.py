"""Execution simulator for paper trading and backtesting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from structlog import get_logger

from arbitrage.events.models import ExecutionIntent, ExecutionResult, OrderBookSnapshot

logger = get_logger(__name__)


@dataclass
class SimulatedFill:
    """Simulated order fill result."""

    success: bool
    filled_price: float
    filled_size: float
    latency_ms: int
    timestamp: datetime
    reason: str | None = None


class ExecutionSimulator:
    """Simulates order execution for paper trading and backtesting.

    Models realistic execution including:
    - Latency (alert-to-order and hedge completion per TDD section 16)
    - Partial fills based on available liquidity
    - Slippage through order book levels
    """

    def __init__(
        self,
        latency_p50_ms: int = 200,
        latency_p95_ms: int = 350,
        hedge_timeout_ms: int = 250,
    ) -> None:
        """Initialize execution simulator.

        Args:
            latency_p50_ms: P50 alert-to-order latency (default 200ms per TDD)
            latency_p95_ms: P95 alert-to-order latency (default 350ms per TDD)
            hedge_timeout_ms: Maximum hedge completion time (default 250ms per TDD)
        """
        self.latency_p50 = latency_p50_ms
        self.latency_p95 = latency_p95_ms
        self.hedge_timeout = hedge_timeout_ms

    def _simulate_latency_ms(self, percentile: float = 0.5) -> int:
        """Simulate execution latency.

        Args:
            percentile: Target percentile (0.5 for median, 0.95 for p95)

        Returns:
            Simulated latency in milliseconds
        """
        import random

        if percentile <= 0.5:
            return random.randint(100, self.latency_p50)
        else:
            return random.randint(self.latency_p50, self.latency_p95)

    def _execute_against_book(
        self,
        book: OrderBookSnapshot,
        side: str,
        target_size: float,
    ) -> SimulatedFill:
        """Simulate execution against an order book.

        Args:
            book: Order book snapshot
            side: "buy" or "sell"
            target_size: Target size in number of contracts/shares

        Returns:
            SimulatedFill with execution results
        """
        levels = book.asks if side == "buy" else book.bids

        if not levels:
            return SimulatedFill(
                success=False,
                filled_price=0.0,
                filled_size=0.0,
                latency_ms=self._simulate_latency_ms(),
                timestamp=book.timestamp,
                reason="No liquidity available",
            )

        # Walk through book levels and fill
        total_cost = 0.0
        total_size = 0.0
        remaining = target_size

        for level in levels[:3]:  # Limit to top 3 levels per TDD
            if remaining <= 0:
                break

            available_size = level.size
            fill_size = min(remaining, available_size)

            total_cost += fill_size * level.price
            total_size += fill_size
            remaining -= fill_size

        if total_size == 0:
            return SimulatedFill(
                success=False,
                filled_price=0.0,
                filled_size=0.0,
                latency_ms=self._simulate_latency_ms(),
                timestamp=book.timestamp,
                reason="Insufficient liquidity",
            )

        avg_price = total_cost / total_size
        latency = self._simulate_latency_ms()

        return SimulatedFill(
            success=True,
            filled_price=avg_price,
            filled_size=total_size,
            latency_ms=latency,
            timestamp=book.timestamp,
        )

    async def simulate_hedged_execution(
        self,
        intent: ExecutionIntent,
        primary_book: OrderBookSnapshot,
        hedge_book: OrderBookSnapshot,
    ) -> ExecutionResult:
        """Simulate a fully hedged pair execution.

        Args:
            intent: Execution intent with trade parameters
            primary_book: Order book for primary market
            hedge_book: Order book for hedge market

        Returns:
            ExecutionResult with success status and metrics
        """
        # Determine trade direction from recommended side
        primary_side = intent.edge.recommended_primary_side
        hedge_side = "sell" if primary_side == "buy" else "buy"

        # Calculate target size in contracts (assuming $100 notional default)
        target_size = intent.max_notional / primary_book.asks[0].price if primary_book.asks else 0

        # Execute primary leg
        primary_fill = self._execute_against_book(primary_book, primary_side, target_size)

        if not primary_fill.success:
            logger.warning("primary_execution_failed", reason=primary_fill.reason)
            return ExecutionResult(
                intent_id=intent.intent_id,
                success=False,
                message=f"Primary failed: {primary_fill.reason}",
            )

        # Execute hedge leg
        hedge_fill = self._execute_against_book(hedge_book, hedge_side, primary_fill.filled_size)

        total_latency_ms = primary_fill.latency_ms + hedge_fill.latency_ms

        # Check hedge completion within timeout
        if total_latency_ms > self.hedge_timeout:
            logger.warning(
                "hedge_timeout",
                latency_ms=total_latency_ms,
                timeout=self.hedge_timeout,
            )
            return ExecutionResult(
                intent_id=intent.intent_id,
                success=False,
                hedge_completed_ms=total_latency_ms,
                message="Hedge timeout exceeded",
            )

        if not hedge_fill.success:
            logger.warning("hedge_execution_failed", reason=hedge_fill.reason)
            return ExecutionResult(
                intent_id=intent.intent_id,
                success=False,
                hedge_completed_ms=total_latency_ms,
                message=f"Hedge failed: {hedge_fill.reason}",
            )

        logger.info(
            "hedged_execution_success",
            primary_price=round(primary_fill.filled_price, 4),
            hedge_price=round(hedge_fill.filled_price, 4),
            size=round(primary_fill.filled_size, 2),
            latency_ms=total_latency_ms,
        )

        return ExecutionResult(
            intent_id=intent.intent_id,
            success=True,
            hedge_completed_ms=total_latency_ms,
            message="Execution successful",
        )


__all__ = ["ExecutionSimulator", "SimulatedFill"]
