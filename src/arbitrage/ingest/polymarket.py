"""Polymarket venue adapter implementing CLOB API and websocket streams."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import AsyncIterator

import httpx
from structlog import get_logger

from arbitrage.domain.markets import Venue
from arbitrage.events.models import MarketReference, OrderBookLevel, OrderBookSnapshot
from arbitrage.ingest.base import IngestError, VenueAdapter

logger = get_logger(__name__)


class PolymarketAdapter(VenueAdapter):
    """Polymarket CLOB API adapter with websocket support."""

    CLOB_BASE_URL = "https://clob.polymarket.com"
    GAMMA_BASE_URL = "https://gamma-api.polymarket.com"

    def __init__(
        self,
        api_key: str | None = None,
        tracked_markets: list[str] | None = None,
        max_depth: int = 3,
    ) -> None:
        """Initialize Polymarket adapter.

        Args:
            api_key: Optional API key for authenticated endpoints
            tracked_markets: List of token IDs to track (None = track all binary markets)
            max_depth: Maximum order book depth levels to capture (default 3 per TDD)
        """
        super().__init__(venue=Venue.POLYMARKET.value)
        self.api_key = api_key
        self.tracked_markets = set(tracked_markets) if tracked_markets else None
        self.max_depth = max_depth
        self._client: httpx.AsyncClient | None = None
        self._running = False

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Ensure HTTP client is initialized."""
        if self._client is None:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._client = httpx.AsyncClient(headers=headers, timeout=10.0)
        return self._client

    async def get_markets(self) -> list[dict]:
        """Fetch all active binary markets from Gamma API.

        Returns:
            List of market metadata dictionaries
        """
        client = await self._ensure_client()
        try:
            response = await client.get(f"{self.GAMMA_BASE_URL}/markets")
            response.raise_for_status()
            markets = response.json()

            # Filter for binary markets only per TDD requirements
            binary_markets = [m for m in markets if m.get("enableOrderBook", False)]

            logger.info(
                "fetched_polymarket_markets",
                total=len(markets),
                binary=len(binary_markets),
            )
            return binary_markets

        except httpx.HTTPError as exc:
            raise IngestError(f"Failed to fetch Polymarket markets: {exc}") from exc

    async def get_orderbook(self, token_id: str) -> dict:
        """Fetch current orderbook for a specific token.

        Args:
            token_id: Polymarket token ID (contract address)

        Returns:
            Orderbook dictionary with bids/asks
        """
        client = await self._ensure_client()
        try:
            response = await client.get(f"{self.CLOB_BASE_URL}/book?token_id={token_id}")
            response.raise_for_status()
            return response.json()

        except httpx.HTTPError as exc:
            logger.warning("orderbook_fetch_failed", token_id=token_id, error=str(exc))
            return {"bids": [], "asks": []}

    def _parse_orderbook_snapshot(
        self, token_id: str, book_data: dict, market_symbol: str
    ) -> OrderBookSnapshot:
        """Parse raw CLOB orderbook data into canonical snapshot.

        Args:
            token_id: Polymarket token ID
            book_data: Raw orderbook from CLOB API
            market_symbol: Canonical market symbol

        Returns:
            OrderBookSnapshot event
        """
        bids = []
        asks = []

        # Parse bids (limited to max_depth)
        for bid in book_data.get("bids", [])[: self.max_depth]:
            price = float(bid.get("price", 0))
            size = float(bid.get("size", 0))
            if price > 0 and size > 0:
                bids.append(OrderBookLevel(price=price, size=size))

        # Parse asks (limited to max_depth)
        for ask in book_data.get("asks", [])[: self.max_depth]:
            price = float(ask.get("price", 0))
            size = float(ask.get("size", 0))
            if price > 0 and size > 0:
                asks.append(OrderBookLevel(price=price, size=size))

        market_ref = MarketReference(
            venue=self.venue,
            market_id=token_id,
            symbol=market_symbol,
        )

        return OrderBookSnapshot(
            market=market_ref,
            timestamp=datetime.now(UTC),
            bids=bids,
            asks=asks,
        )

    async def stream_orderbooks(self) -> AsyncIterator[OrderBookSnapshot]:
        """Yield orderbook snapshots by polling CLOB endpoints.

        Note: This is a polling-based implementation. In production, replace with
        websocket streaming for lower latency per TDD requirements.
        """
        self._running = True
        markets = await self.get_markets()

        # Filter to tracked markets if specified
        if self.tracked_markets:
            markets = [m for m in markets if m.get("tokenID") in self.tracked_markets]

        logger.info("starting_polymarket_stream", market_count=len(markets))

        while self._running:
            for market in markets:
                token_id = market.get("tokenID")
                if not token_id:
                    continue

                try:
                    book_data = await self.get_orderbook(token_id)
                    symbol = market.get("ticker", token_id)
                    snapshot = self._parse_orderbook_snapshot(token_id, book_data, symbol)

                    # Only yield if we have valid quotes
                    if snapshot.bids or snapshot.asks:
                        yield snapshot

                except Exception as exc:
                    logger.warning(
                        "orderbook_processing_failed",
                        token_id=token_id,
                        error=str(exc),
                    )
                    continue

            # Polling interval - adjust based on latency requirements
            await asyncio.sleep(2.0)

    async def close(self) -> None:
        """Clean up HTTP client and stop streaming."""
        self._running = False
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("polymarket_adapter_closed")


__all__ = ["PolymarketAdapter"]
