"""Order book depth model for slippage estimation."""

from __future__ import annotations

from dataclasses import dataclass

from structlog import get_logger

from arbitrage.events.models import OrderBookSnapshot
from arbitrage.markets.pairs import MarketPair

logger = get_logger(__name__)


@dataclass
class DepthAnalysis:
    """Analysis of order book depth for a market pair."""

    primary_bid_depth_usd: float
    primary_ask_depth_usd: float
    hedge_bid_depth_usd: float
    hedge_ask_depth_usd: float
    primary_best_bid: float
    primary_best_ask: float
    hedge_best_bid: float
    hedge_best_ask: float


class DepthModel:
    """Estimates achievable size and slippage from order book depth.

    Implements depth analysis from TDD section 6.1. Uses top 3 levels
    to calculate expected slippage for a given trade size.
    """

    def __init__(self, max_levels: int = 3) -> None:
        """Initialize depth model.

        Args:
            max_levels: Maximum order book levels to consider (default 3 per TDD)
        """
        self.max_levels = max_levels

    def analyze_depth(
        self,
        primary_book: OrderBookSnapshot,
        hedge_book: OrderBookSnapshot,
    ) -> DepthAnalysis:
        """Analyze order book depth for both sides of a pair.

        Args:
            primary_book: Order book for primary market
            hedge_book: Order book for hedge market

        Returns:
            DepthAnalysis with cumulative depth and best prices
        """
        # Calculate cumulative depth for primary (limit to max_levels)
        primary_bid_depth = sum(
            level.price * level.size
            for level in primary_book.bids[: self.max_levels]
        )
        primary_ask_depth = sum(
            level.price * level.size
            for level in primary_book.asks[: self.max_levels]
        )

        # Calculate cumulative depth for hedge
        hedge_bid_depth = sum(
            level.price * level.size
            for level in hedge_book.bids[: self.max_levels]
        )
        hedge_ask_depth = sum(
            level.price * level.size
            for level in hedge_book.asks[: self.max_levels]
        )

        # Extract best prices
        primary_best_bid = primary_book.bids[0].price if primary_book.bids else 0.0
        primary_best_ask = primary_book.asks[0].price if primary_book.asks else 1.0
        hedge_best_bid = hedge_book.bids[0].price if hedge_book.bids else 0.0
        hedge_best_ask = hedge_book.asks[0].price if hedge_book.asks else 1.0

        return DepthAnalysis(
            primary_bid_depth_usd=primary_bid_depth,
            primary_ask_depth_usd=primary_ask_depth,
            hedge_bid_depth_usd=hedge_bid_depth,
            hedge_ask_depth_usd=hedge_ask_depth,
            primary_best_bid=primary_best_bid,
            primary_best_ask=primary_best_ask,
            hedge_best_bid=hedge_best_bid,
            hedge_best_ask=hedge_best_ask,
        )

    def _calculate_vwap(self, levels: list, target_size_usd: float) -> float:
        """Calculate volume-weighted average price for a target size.

        Args:
            levels: List of OrderBookLevel objects (bids or asks)
            target_size_usd: Target notional in USD

        Returns:
            VWAP price, or 0 if insufficient liquidity
        """
        if not levels:
            return 0.0

        total_cost = 0.0
        total_size = 0.0
        remaining = target_size_usd

        for level in levels[: self.max_levels]:
            level_notional = level.price * level.size
            if level_notional <= remaining:
                total_cost += level_notional
                total_size += level.size
                remaining -= level_notional
            else:
                # Partial fill of this level
                partial_size = remaining / level.price
                total_cost += remaining
                total_size += partial_size
                remaining = 0.0
                break

        if total_size == 0:
            return 0.0

        return total_cost / total_size

    def expected_slippage_cents(
        self,
        pair: MarketPair,
        size_usd: float,
        primary_book: OrderBookSnapshot | None = None,
        hedge_book: OrderBookSnapshot | None = None,
    ) -> float:
        """Estimate expected slippage for a pair trade.

        Args:
            pair: Market pair being traded
            size_usd: Trade size per side in USD
            primary_book: Current order book for primary market
            hedge_book: Current order book for hedge market

        Returns:
            Expected total slippage in cents across both legs
        """
        if not primary_book or not hedge_book:
            # No book data, use conservative estimate
            logger.warning("no_orderbook_data", pair_primary=pair.primary.symbol)
            return size_usd * 0.01 * 100  # 1% slippage estimate

        depth = self.analyze_depth(primary_book, hedge_book)

        # For a typical arbitrage: buy on one side, sell on the other
        # Assume we're hitting the ask on primary and the bid on hedge
        # (or vice versa depending on which direction has the edge)

        # Calculate VWAP for primary ask side
        primary_vwap = self._calculate_vwap(primary_book.asks, size_usd)
        if primary_vwap == 0:
            logger.warning("insufficient_primary_liquidity", size=size_usd)
            return size_usd * 0.02 * 100  # 2% penalty for no liquidity

        # Calculate VWAP for hedge bid side
        hedge_vwap = self._calculate_vwap(hedge_book.bids, size_usd)
        if hedge_vwap == 0:
            logger.warning("insufficient_hedge_liquidity", size=size_usd)
            return size_usd * 0.02 * 100

        # Slippage is difference between VWAP and best price
        primary_slippage = abs(primary_vwap - depth.primary_best_ask) * size_usd / depth.primary_best_ask
        hedge_slippage = abs(hedge_vwap - depth.hedge_best_bid) * size_usd / depth.hedge_best_bid

        total_slippage_usd = primary_slippage + hedge_slippage

        logger.debug(
            "slippage_estimate",
            primary_cents=round(primary_slippage * 100, 2),
            hedge_cents=round(hedge_slippage * 100, 2),
            total_cents=round(total_slippage_usd * 100, 2),
        )

        return total_slippage_usd * 100  # Convert to cents

    def max_tradeable_size(
        self,
        primary_book: OrderBookSnapshot,
        hedge_book: OrderBookSnapshot,
        side: str = "arb",
    ) -> float:
        """Calculate maximum tradeable size given current depth.

        Args:
            primary_book: Order book for primary market
            hedge_book: Order book for hedge market
            side: Trade direction ("arb" for typical arbitrage)

        Returns:
            Maximum notional size in USD
        """
        depth = self.analyze_depth(primary_book, hedge_book)

        # For arbitrage, we need liquidity on opposite sides
        # Minimum of ask depth on primary and bid depth on hedge
        max_size = min(depth.primary_ask_depth_usd, depth.hedge_bid_depth_usd)

        logger.debug(
            "max_size_calculation",
            primary_ask_depth=round(depth.primary_ask_depth_usd, 2),
            hedge_bid_depth=round(depth.hedge_bid_depth_usd, 2),
            max_size=round(max_size, 2),
        )

        return max_size


__all__ = ["DepthAnalysis", "DepthModel"]
