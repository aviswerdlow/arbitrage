"""Signal computation for mispricing detection and lead-lag analysis."""

from arbitrage.signals.depth import DepthAnalysis, DepthModel
from arbitrage.signals.friction import (
    FrictionModel,
    FrictionPack,
    KalshiFeeCalculator,
    PolymarketFeeCalculator,
    VenueFees,
)
from arbitrage.signals.leadlag import LeadLagAnalyzer, LeadLagResult, PriceBar
from arbitrage.signals.service import (
    DepthModel as DepthModelProtocol,
    FrictionModel as FrictionModelProtocol,
    SignalRequest,
    SignalService,
)

__all__ = [
    "DepthAnalysis",
    "DepthModel",
    "DepthModelProtocol",
    "FrictionModel",
    "FrictionModelProtocol",
    "FrictionPack",
    "KalshiFeeCalculator",
    "LeadLagAnalyzer",
    "LeadLagResult",
    "PolymarketFeeCalculator",
    "PriceBar",
    "SignalRequest",
    "SignalService",
    "VenueFees",
]
