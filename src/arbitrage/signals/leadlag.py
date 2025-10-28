"""Lead-lag detection using rolling cross-correlation."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
from structlog import get_logger

logger = get_logger(__name__)


@dataclass
class PriceBar:
    """5-second price bar for cross-correlation analysis."""

    timestamp: datetime
    venue: str
    market_id: str
    mid_price: float  # Mid-price (bid + ask) / 2


@dataclass
class LeadLagResult:
    """Result of lead-lag analysis."""

    leader: str | None  # Venue that leads (e.g., "polymarket" or "kalshi")
    lag_seconds: float  # Detected lag in seconds
    correlation: float  # Correlation coefficient at optimal lag
    confidence: float  # Confidence in result [0, 1]
    stable: bool  # Whether leader has been consistent


class LeadLagAnalyzer:
    """Detects price leadership using rolling cross-correlation on 5-sec bars.

    Implements TDD section 6.2: Uses 5-second bars and 10-minute rolling window
    with stability filter (same leader in 3 of last 4 windows).
    """

    def __init__(
        self,
        bar_interval_seconds: int = 5,
        window_minutes: int = 10,
        stability_window: int = 4,
        min_correlation: float = 0.3,
    ) -> None:
        """Initialize lead-lag analyzer.

        Args:
            bar_interval_seconds: Bar interval (default 5 seconds per TDD)
            window_minutes: Rolling window size (default 10 minutes per TDD)
            stability_window: Number of windows to check for stability (default 4)
            min_correlation: Minimum correlation to consider significant (default 0.3)
        """
        self.bar_interval = timedelta(seconds=bar_interval_seconds)
        self.window_size = timedelta(minutes=window_minutes)
        self.stability_window = stability_window
        self.min_correlation = min_correlation

        # Storage for price bars per market pair
        self._bars: dict[str, deque[PriceBar]] = {}
        self._leader_history: dict[str, deque[str]] = {}

    def _get_pair_key(self, market_a: str, market_b: str) -> str:
        """Generate consistent key for a market pair."""
        return f"{market_a}:{market_b}"

    def add_price_update(
        self,
        venue: str,
        market_id: str,
        timestamp: datetime,
        bid: float,
        ask: float,
        pair_key: str,
    ) -> None:
        """Add a price update to the analyzer.

        Args:
            venue: Venue name (e.g., "polymarket", "kalshi")
            market_id: Market identifier
            timestamp: Update timestamp
            bid: Best bid price
            ask: Best ask price
            pair_key: Pair key for grouping related markets
        """
        mid_price = (bid + ask) / 2.0

        bar = PriceBar(
            timestamp=timestamp,
            venue=venue,
            market_id=market_id,
            mid_price=mid_price,
        )

        if pair_key not in self._bars:
            self._bars[pair_key] = deque(maxlen=1000)  # Keep last ~83 minutes at 5s bars

        self._bars[pair_key].append(bar)

    def _build_price_series(
        self,
        bars: list[PriceBar],
        venue: str,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Build time series of mid-prices for a specific venue.

        Args:
            bars: List of price bars
            venue: Venue to filter for

        Returns:
            Tuple of (timestamps_array, prices_array)
        """
        venue_bars = [b for b in bars if b.venue == venue]
        if not venue_bars:
            return np.array([]), np.array([])

        timestamps = np.array([b.timestamp.timestamp() for b in venue_bars])
        prices = np.array([b.mid_price for b in venue_bars])

        return timestamps, prices

    def _resample_to_bars(
        self,
        timestamps: np.ndarray,
        prices: np.ndarray,
        bar_interval_sec: int,
    ) -> np.ndarray:
        """Resample irregular ticks into regular bars.

        Args:
            timestamps: Unix timestamps
            prices: Price values
            bar_interval_sec: Bar interval in seconds

        Returns:
            Array of bar prices (last price in each interval)
        """
        if len(timestamps) == 0:
            return np.array([])

        start_time = timestamps[0]
        end_time = timestamps[-1]
        num_bars = int((end_time - start_time) / bar_interval_sec) + 1

        bars = []
        for i in range(num_bars):
            bar_start = start_time + i * bar_interval_sec
            bar_end = bar_start + bar_interval_sec

            # Get prices in this bar interval
            mask = (timestamps >= bar_start) & (timestamps < bar_end)
            bar_prices = prices[mask]

            if len(bar_prices) > 0:
                bars.append(bar_prices[-1])  # Use last price
            elif len(bars) > 0:
                bars.append(bars[-1])  # Forward-fill if no update
            else:
                bars.append(np.nan)

        return np.array(bars)

    def _compute_cross_correlation(
        self,
        series_a: np.ndarray,
        series_b: np.ndarray,
        max_lag: int = 12,
    ) -> tuple[int, float]:
        """Compute cross-correlation and find optimal lag.

        Args:
            series_a: First price series
            series_b: Second price series
            max_lag: Maximum lag to test in bars (default 12 = 60 seconds)

        Returns:
            Tuple of (optimal_lag_bars, correlation_coefficient)
            Positive lag means series_a leads series_b
        """
        if len(series_a) < 10 or len(series_b) < 10:
            return 0, 0.0

        # Normalize series (z-score)
        a_norm = (series_a - np.nanmean(series_a)) / (np.nanstd(series_a) + 1e-10)
        b_norm = (series_b - np.nanmean(series_b)) / (np.nanstd(series_b) + 1e-10)

        correlations = []
        for lag in range(-max_lag, max_lag + 1):
            if lag < 0:
                # series_b leads series_a
                corr = np.corrcoef(a_norm[:lag], b_norm[-lag:])[0, 1]
            elif lag > 0:
                # series_a leads series_b
                corr = np.corrcoef(a_norm[lag:], b_norm[:-lag])[0, 1]
            else:
                corr = np.corrcoef(a_norm, b_norm)[0, 1]

            if not np.isnan(corr):
                correlations.append((lag, corr))

        if not correlations:
            return 0, 0.0

        # Find lag with maximum absolute correlation
        optimal_lag, max_corr = max(correlations, key=lambda x: abs(x[1]))

        return optimal_lag, max_corr

    def analyze(
        self,
        pair_key: str,
        venue_a: str,
        venue_b: str,
    ) -> LeadLagResult:
        """Analyze lead-lag relationship for a market pair.

        Args:
            pair_key: Pair key to analyze
            venue_a: First venue (e.g., "polymarket")
            venue_b: Second venue (e.g., "kalshi")

        Returns:
            LeadLagResult with detected leader and confidence
        """
        if pair_key not in self._bars or len(self._bars[pair_key]) < 20:
            return LeadLagResult(
                leader=None,
                lag_seconds=0.0,
                correlation=0.0,
                confidence=0.0,
                stable=False,
            )

        # Get bars within rolling window
        recent_bars = list(self._bars[pair_key])
        cutoff_time = recent_bars[-1].timestamp - self.window_size
        windowed_bars = [b for b in recent_bars if b.timestamp >= cutoff_time]

        # Build price series for each venue
        ts_a, prices_a = self._build_price_series(windowed_bars, venue_a)
        ts_b, prices_b = self._build_price_series(windowed_bars, venue_b)

        # Resample to regular bars
        bar_sec = int(self.bar_interval.total_seconds())
        bars_a = self._resample_to_bars(ts_a, prices_a, bar_sec)
        bars_b = self._resample_to_bars(ts_b, prices_b, bar_sec)

        # Compute cross-correlation
        optimal_lag, correlation = self._compute_cross_correlation(bars_a, bars_b)

        # Determine leader
        leader = None
        if abs(correlation) >= self.min_correlation:
            if optimal_lag > 0:
                leader = venue_a
            elif optimal_lag < 0:
                leader = venue_b
                optimal_lag = abs(optimal_lag)

        # Update leader history
        if pair_key not in self._leader_history:
            self._leader_history[pair_key] = deque(maxlen=self.stability_window)

        if leader:
            self._leader_history[pair_key].append(leader)

        # Check stability (same leader in 3 of last 4 windows per TDD)
        stable = False
        if len(self._leader_history[pair_key]) >= 3:
            history = list(self._leader_history[pair_key])
            if leader and history.count(leader) >= 3:
                stable = True

        confidence = min(abs(correlation), 1.0) if stable else abs(correlation) * 0.5

        logger.debug(
            "leadlag_analysis",
            pair=pair_key,
            leader=leader,
            lag_bars=optimal_lag,
            correlation=round(correlation, 3),
            stable=stable,
        )

        return LeadLagResult(
            leader=leader,
            lag_seconds=optimal_lag * bar_sec,
            correlation=correlation,
            confidence=confidence,
            stable=stable,
        )


__all__ = ["LeadLagAnalyzer", "LeadLagResult", "PriceBar"]
