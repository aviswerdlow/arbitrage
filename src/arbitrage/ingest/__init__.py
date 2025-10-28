"""Ingestion adapters and service coordination."""

from arbitrage.ingest.base import IngestError, IngestService, VenueAdapter
from arbitrage.ingest.kalshi import KalshiAdapter
from arbitrage.ingest.polymarket import PolymarketAdapter

__all__ = [
    "IngestError",
    "IngestService",
    "KalshiAdapter",
    "PolymarketAdapter",
    "VenueAdapter",
]
