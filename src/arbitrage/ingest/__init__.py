"""Ingestion adapters and service coordination."""

from arbitrage.ingest.base import IngestError, IngestService, VenueAdapter
from arbitrage.ingest.kalshi import KalshiAdapter
from arbitrage.ingest.kalshi_ws import KalshiWebsocketAdapter
from arbitrage.ingest.polymarket import PolymarketAdapter
from arbitrage.ingest.polymarket_ws import PolymarketWebsocketAdapter

__all__ = [
    "IngestError",
    "IngestService",
    "KalshiAdapter",
    "KalshiWebsocketAdapter",
    "PolymarketAdapter",
    "PolymarketWebsocketAdapter",
    "VenueAdapter",
]
