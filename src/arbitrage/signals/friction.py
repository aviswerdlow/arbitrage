"""Fee and friction models for accurate edge computation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from structlog import get_logger

from arbitrage.markets.pairs import MarketPair

logger = get_logger(__name__)


@dataclass
class VenueFees:
    """Fee structure for a specific venue."""

    taker_fee_pct: float  # As decimal (e.g., 0.02 for 2%)
    maker_fee_pct: float
    profit_fee_pct: float  # Fee on profits (if applicable)
    min_fee: float = 0.0  # Minimum fee in dollars


@dataclass
class FrictionPack:
    """Complete friction cost model version.

    Represents all costs beyond exchange fees per TDD section 9.
    """

    gas_cost_usd: float = 0.0  # Gas cost per transaction
    bridge_cost_usd: float = 0.0  # Bridge cost for cross-chain (Polymarket)
    onramp_fee_pct: float = 0.0  # On-ramp fee percentage
    fx_spread_pct: float = 0.0  # FX spread for USD conversions
    version_hash: str = "v1"  # Version identifier for tracking


class VenueFeeCalculator(Protocol):
    """Protocol for venue-specific fee calculations."""

    def calculate_taker_fee(self, notional: float) -> float:
        """Calculate taker fee for a given notional amount."""
        ...

    def calculate_profit_fee(self, profit: float) -> float:
        """Calculate profit fee (if applicable)."""
        ...


class PolymarketFeeCalculator:
    """Polymarket-specific fee calculator.

    Polymarket uses a 2% taker fee with no maker fees in most cases.
    Additionally charges profit fees on net winnings.
    """

    def __init__(self, fees: VenueFees | None = None) -> None:
        """Initialize Polymarket fee calculator.

        Args:
            fees: Custom fee structure (default uses current Polymarket fees)
        """
        self.fees = fees or VenueFees(
            taker_fee_pct=0.02,  # 2% taker fee
            maker_fee_pct=0.0,  # No maker fee
            profit_fee_pct=0.02,  # 2% on profits
        )

    def calculate_taker_fee(self, notional: float) -> float:
        """Calculate taker fee for Polymarket order.

        Args:
            notional: Order notional in USD

        Returns:
            Fee amount in USD
        """
        return notional * self.fees.taker_fee_pct

    def calculate_profit_fee(self, profit: float) -> float:
        """Calculate profit fee (charged on net winnings).

        Args:
            profit: Gross profit in USD

        Returns:
            Fee amount in USD
        """
        if profit <= 0:
            return 0.0
        return profit * self.fees.profit_fee_pct


class KalshiFeeCalculator:
    """Kalshi-specific fee calculator.

    Kalshi uses tiered fees based on volume, with separate taker/maker rates.
    No profit fees in current structure.
    """

    def __init__(self, fees: VenueFees | None = None) -> None:
        """Initialize Kalshi fee calculator.

        Args:
            fees: Custom fee structure (default uses typical Kalshi retail fees)
        """
        self.fees = fees or VenueFees(
            taker_fee_pct=0.007,  # 0.7% taker (retail tier)
            maker_fee_pct=0.0,  # Maker rebate in some cases, simplified to 0
            profit_fee_pct=0.0,  # No profit fees
        )

    def calculate_taker_fee(self, notional: float) -> float:
        """Calculate taker fee for Kalshi order.

        Args:
            notional: Order notional in USD (contracts * price in dollars)

        Returns:
            Fee amount in USD
        """
        # Kalshi charges per contract, but we simplify to percentage
        return notional * self.fees.taker_fee_pct

    def calculate_profit_fee(self, profit: float) -> float:
        """Kalshi does not charge profit fees."""
        return 0.0


class FrictionModel:
    """Calculates total friction costs for a trade package per TDD section 9.

    Combines exchange fees, gas, bridge, on-ramp, and FX costs.
    """

    def __init__(
        self,
        poly_calculator: PolymarketFeeCalculator | None = None,
        kalshi_calculator: KalshiFeeCalculator | None = None,
        friction_pack: FrictionPack | None = None,
    ) -> None:
        """Initialize friction model.

        Args:
            poly_calculator: Polymarket fee calculator
            kalshi_calculator: Kalshi fee calculator
            friction_pack: Friction cost parameters
        """
        self.poly_calc = poly_calculator or PolymarketFeeCalculator()
        self.kalshi_calc = kalshi_calculator or KalshiFeeCalculator()
        self.friction_pack = friction_pack or FrictionPack(
            gas_cost_usd=2.0,  # Typical gas cost for Polygon transaction
            bridge_cost_usd=5.0,  # Bridge cost if needed
            onramp_fee_pct=0.005,  # 0.5% on-ramp fee
            fx_spread_pct=0.001,  # 0.1% FX spread
        )

    def total_cost_cents(self, pair: MarketPair, size_usd: float) -> float:
        """Calculate total friction cost for a hedged pair trade.

        Args:
            pair: Market pair being traded
            size_usd: Trade size in USD (per side)

        Returns:
            Total friction cost in cents
        """
        # Primary side (usually Polymarket)
        primary_venue = pair.primary.venue
        if primary_venue == "polymarket":
            primary_taker_fee = self.poly_calc.calculate_taker_fee(size_usd)
            # Assume we win, so pay profit fee on the spread
            primary_profit_fee = self.poly_calc.calculate_profit_fee(size_usd * 0.025)  # Rough estimate
        else:
            primary_taker_fee = self.kalshi_calc.calculate_taker_fee(size_usd)
            primary_profit_fee = 0.0

        # Hedge side (usually Kalshi)
        hedge_venue = pair.hedge.venue
        if hedge_venue == "kalshi":
            hedge_taker_fee = self.kalshi_calc.calculate_taker_fee(size_usd)
            hedge_profit_fee = 0.0
        else:
            hedge_taker_fee = self.poly_calc.calculate_taker_fee(size_usd)
            hedge_profit_fee = self.poly_calc.calculate_profit_fee(size_usd * 0.025)

        # Sum all costs
        exchange_fees = primary_taker_fee + hedge_taker_fee + primary_profit_fee + hedge_profit_fee

        # Add friction costs
        gas_cost = self.friction_pack.gas_cost_usd * 2  # Two transactions
        bridge_cost = self.friction_pack.bridge_cost_usd if primary_venue == "polymarket" else 0.0
        onramp_cost = size_usd * self.friction_pack.onramp_fee_pct
        fx_cost = size_usd * self.friction_pack.fx_spread_pct

        total_usd = exchange_fees + gas_cost + bridge_cost + onramp_cost + fx_cost

        logger.debug(
            "friction_breakdown",
            exchange=round(exchange_fees, 2),
            gas=round(gas_cost, 2),
            bridge=round(bridge_cost, 2),
            onramp=round(onramp_cost, 2),
            fx=round(fx_cost, 2),
            total_cents=round(total_usd * 100, 2),
        )

        return total_usd * 100  # Convert to cents


__all__ = [
    "FrictionModel",
    "FrictionPack",
    "KalshiFeeCalculator",
    "PolymarketFeeCalculator",
    "VenueFees",
]
