"""Backtest engine for historical strategy simulation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
from structlog import get_logger

from arbitrage.events.models import OrderBookSnapshot
from arbitrage.markets.pairs import MarketPair
from arbitrage.signals import DepthModel, FrictionModel, SignalService

logger = get_logger(__name__)


@dataclass
class Trade:
    """Record of a simulated trade."""

    timestamp: datetime
    pair_id: str
    primary_market: str
    hedge_market: str
    entry_edge_cents: float
    realized_edge_cents: float
    slippage_cents: float
    fees_cents: float
    size_usd: float
    pnl_cents: float


@dataclass
class BacktestMetrics:
    """Aggregate metrics from backtest run per TDD section 14.5."""

    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl_cents: float = 0.0
    gross_pnl_cents: float = 0.0
    total_fees_cents: float = 0.0
    total_slippage_cents: float = 0.0
    avg_entry_edge_cents: float = 0.0
    avg_realized_edge_cents: float = 0.0
    avg_slippage_cents: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown_cents: float = 0.0
    hit_rate: float = 0.0
    avg_trade_size_usd: float = 0.0

    def __str__(self) -> str:
        return f"""Backtest Metrics:
  Total Trades: {self.total_trades}
  Hit Rate: {self.hit_rate:.1%}
  Total PnL: ${self.total_pnl_cents / 100:.2f}
  Gross PnL: ${self.gross_pnl_cents / 100:.2f}
  Total Fees: ${self.total_fees_cents / 100:.2f}
  Total Slippage: ${self.total_slippage_cents / 100:.2f}
  Avg Entry Edge: {self.avg_entry_edge_cents:.2f}¢
  Avg Realized Edge: {self.avg_realized_edge_cents:.2f}¢
  Avg Slippage: {self.avg_slippage_cents:.2f}¢
  Sharpe Ratio: {self.sharpe_ratio:.2f}
  Max Drawdown: ${abs(self.max_drawdown_cents) / 100:.2f}
"""


@dataclass
class BacktestResult:
    """Complete backtest results with trades and metrics."""

    metrics: BacktestMetrics
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    timestamps: list[datetime] = field(default_factory=list)


class BacktestEngine:
    """Historical simulation engine with replay capability.

    Implements TDD section 14.5: Loads historical data, replays through
    strategy logic, and computes acceptance metrics (Sharpe ≥ 2.0).
    """

    def __init__(
        self,
        signal_service: SignalService,
        friction_model: FrictionModel,
        depth_model: DepthModel,
        min_edge_cents: float = 2.5,
        default_trade_size: float = 100.0,
    ) -> None:
        """Initialize backtest engine.

        Args:
            signal_service: Signal computation service
            friction_model: Fee and friction model
            depth_model: Slippage estimation model
            min_edge_cents: Minimum edge threshold (default 2.5¢ per TDD)
            default_trade_size: Default trade size in USD
        """
        self.signal_service = signal_service
        self.friction_model = friction_model
        self.depth_model = depth_model
        self.min_edge_cents = min_edge_cents
        self.default_trade_size = default_trade_size

    def _simulate_trade_execution(
        self,
        pair: MarketPair,
        primary_book: OrderBookSnapshot,
        hedge_book: OrderBookSnapshot,
        entry_edge_cents: float,
        size_usd: float,
    ) -> Trade:
        """Simulate execution of a trade with realistic fills.

        Args:
            pair: Market pair being traded
            primary_book: Primary market order book
            hedge_book: Hedge market order book
            entry_edge_cents: Computed edge at entry
            size_usd: Trade size

        Returns:
            Trade record with simulated outcomes
        """
        # Calculate friction costs
        fees_cents = self.friction_model.total_cost_cents(pair, size_usd)

        # Calculate slippage
        slippage_cents = self.depth_model.expected_slippage_cents(
            pair, size_usd, primary_book, hedge_book
        )

        # Realized edge = entry edge - fees - slippage
        realized_edge_cents = entry_edge_cents - fees_cents - slippage_cents

        # PnL is the realized edge times size (in cents per dollar)
        pnl_cents = realized_edge_cents * (size_usd / 100)

        return Trade(
            timestamp=primary_book.timestamp,
            pair_id=f"{pair.primary.market_id}:{pair.hedge.market_id}",
            primary_market=pair.primary.symbol,
            hedge_market=pair.hedge.symbol,
            entry_edge_cents=entry_edge_cents,
            realized_edge_cents=realized_edge_cents,
            slippage_cents=slippage_cents,
            fees_cents=fees_cents,
            size_usd=size_usd,
            pnl_cents=pnl_cents,
        )

    def _calculate_metrics(self, trades: list[Trade]) -> BacktestMetrics:
        """Calculate aggregate metrics from trade history.

        Args:
            trades: List of executed trades

        Returns:
            BacktestMetrics with all statistics
        """
        if not trades:
            return BacktestMetrics()

        # Basic counts
        total_trades = len(trades)
        winning_trades = sum(1 for t in trades if t.pnl_cents > 0)
        losing_trades = sum(1 for t in trades if t.pnl_cents <= 0)

        # PnL metrics
        total_pnl = sum(t.pnl_cents for t in trades)
        gross_pnl = sum(t.entry_edge_cents * (t.size_usd / 100) for t in trades)
        total_fees = sum(t.fees_cents for t in trades)
        total_slippage = sum(t.slippage_cents for t in trades)

        # Averages
        avg_entry_edge = np.mean([t.entry_edge_cents for t in trades])
        avg_realized_edge = np.mean([t.realized_edge_cents for t in trades])
        avg_slippage = np.mean([t.slippage_cents for t in trades])
        avg_size = np.mean([t.size_usd for t in trades])

        # Sharpe ratio calculation
        # Assume daily returns (group trades by day)
        daily_returns = {}
        for trade in trades:
            day = trade.timestamp.date()
            if day not in daily_returns:
                daily_returns[day] = 0.0
            daily_returns[day] += trade.pnl_cents / 100  # Convert to dollars

        returns_array = np.array(list(daily_returns.values()))
        if len(returns_array) > 1:
            mean_return = np.mean(returns_array)
            std_return = np.std(returns_array)
            sharpe = (mean_return / std_return * np.sqrt(252)) if std_return > 0 else 0.0
        else:
            sharpe = 0.0

        # Max drawdown
        equity_curve = np.cumsum([t.pnl_cents for t in trades])
        running_max = np.maximum.accumulate(equity_curve)
        drawdown = equity_curve - running_max
        max_drawdown = float(np.min(drawdown)) if len(drawdown) > 0 else 0.0

        # Hit rate
        hit_rate = winning_trades / total_trades if total_trades > 0 else 0.0

        return BacktestMetrics(
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            total_pnl_cents=total_pnl,
            gross_pnl_cents=gross_pnl,
            total_fees_cents=total_fees,
            total_slippage_cents=total_slippage,
            avg_entry_edge_cents=avg_entry_edge,
            avg_realized_edge_cents=avg_realized_edge,
            avg_slippage_cents=avg_slippage,
            sharpe_ratio=sharpe,
            max_drawdown_cents=max_drawdown,
            hit_rate=hit_rate,
            avg_trade_size_usd=avg_size,
        )

    def run(
        self,
        pairs: list[MarketPair],
        orderbook_snapshots: dict[str, list[OrderBookSnapshot]],
    ) -> BacktestResult:
        """Run backtest on historical data.

        Args:
            pairs: List of validated market pairs
            orderbook_snapshots: Dict mapping market_id to list of historical snapshots

        Returns:
            BacktestResult with trades and metrics
        """
        logger.info("starting_backtest", pairs=len(pairs))

        trades = []
        equity_curve = [0.0]
        timestamps = []

        for pair in pairs:
            primary_id = pair.primary.market_id
            hedge_id = pair.hedge.market_id

            if primary_id not in orderbook_snapshots or hedge_id not in orderbook_snapshots:
                logger.warning("missing_orderbook_data", pair_primary=primary_id)
                continue

            primary_books = orderbook_snapshots[primary_id]
            hedge_books = orderbook_snapshots[hedge_id]

            # Simple matching: assume snapshots are time-aligned
            min_length = min(len(primary_books), len(hedge_books))

            for i in range(min_length):
                primary_book = primary_books[i]
                hedge_book = hedge_books[i]

                # Calculate gross edge (simplified)
                if not primary_book.asks or not hedge_book.bids:
                    continue

                primary_ask = primary_book.asks[0].price
                hedge_bid = hedge_book.bids[0].price

                # Gross edge = price difference
                gross_edge_cents = (hedge_bid - primary_ask) * 100

                # Check if edge exceeds threshold
                if gross_edge_cents < self.min_edge_cents:
                    continue

                # Simulate trade execution
                trade = self._simulate_trade_execution(
                    pair,
                    primary_book,
                    hedge_book,
                    gross_edge_cents,
                    self.default_trade_size,
                )

                trades.append(trade)
                equity_curve.append(equity_curve[-1] + trade.pnl_cents / 100)
                timestamps.append(trade.timestamp)

                logger.debug(
                    "trade_executed",
                    pair=trade.pair_id,
                    edge=round(trade.entry_edge_cents, 2),
                    realized=round(trade.realized_edge_cents, 2),
                    pnl=round(trade.pnl_cents / 100, 2),
                )

        metrics = self._calculate_metrics(trades)

        logger.info(
            "backtest_complete",
            trades=len(trades),
            sharpe=round(metrics.sharpe_ratio, 2),
            pnl=round(metrics.total_pnl_cents / 100, 2),
        )

        return BacktestResult(
            metrics=metrics,
            trades=trades,
            equity_curve=equity_curve,
            timestamps=timestamps,
        )


__all__ = ["BacktestEngine", "BacktestMetrics", "BacktestResult", "Trade"]
