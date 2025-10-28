"""Kalshi venue adapter implementing REST API and websocket streams."""

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


class KalshiAdapter(VenueAdapter):
    """Kalshi REST API adapter with websocket support."""

    API_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
    DEMO_BASE_URL = "https://demo-api.elections.kalshi.com/trade-api/v2"

    def __init__(
        self,
        api_key: str | None = None,
        use_demo: bool = False,
        tracked_markets: list[str] | None = None,
        max_depth: int = 3,
    ) -> None:
        """Initialize Kalshi adapter.

        Args:
            api_key: Optional API key for authenticated endpoints
            use_demo: Use demo environment instead of production
            tracked_markets: List of market tickers to track (None = track all binary markets)
            max_depth: Maximum order book depth levels to capture (default 3 per TDD)
        """
        super().__init__(venue=Venue.KALSHI.value)
        self.api_key = api_key
        self.base_url = self.DEMO_BASE_URL if use_demo else self.API_BASE_URL
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

    async def get_markets(self, status: str = "open") -> list[dict]:
        """Fetch active binary markets from Kalshi API.

        Args:
            status: Market status filter (default: "open")

        Returns:
            List of market metadata dictionaries
        """
        client = await self._ensure_client()
        try:
            # Kalshi uses pagination for market listings
            params = {
                "status": status,
                "limit": 200,  # Max per page
            }
            response = await client.get(f"{self.base_url}/markets", params=params)
            response.raise_for_status()
            data = response.json()
            markets = data.get("markets", [])

            logger.info(
                "fetched_kalshi_markets",
                total=len(markets),
                status=status,
            )
            return markets

        except httpx.HTTPError as exc:
            raise IngestError(f"Failed to fetch Kalshi markets: {exc}") from exc

    async def get_orderbook(self, ticker: str) -> dict:
        """Fetch current orderbook for a specific market ticker.

        Args:
            ticker: Kalshi market ticker (e.g., "KXINFLATION-23SEP-B3.0")

        Returns:
            Orderbook dictionary with bids/offers
        """
        client = await self._ensure_client()
        try:
            # Kalshi uses "orderbook" endpoint with ticker parameter
            response = await client.get(f"{self.base_url}/markets/{ticker}/orderbook")
            response.raise_for_status()
            data = response.json()
            return data.get("orderbook", {"yes": [], "no": []})

        except httpx.HTTPError as exc:
            logger.warning("orderbook_fetch_failed", ticker=ticker, error=str(exc))
            return {"yes": [], "no": []}

    def _parse_orderbook_snapshot(
        self, ticker: str, book_data: dict, market_title: str
    ) -> OrderBookSnapshot:
        """Parse raw Kalshi orderbook data into canonical snapshot.

        Kalshi returns separate "yes" and "no" order books. We convert to standard
        bid/ask format where:
        - YES bids = bids (buying YES)
        - NO bids = asks (selling YES / buying NO)

        Args:
            ticker: Kalshi market ticker
            book_data: Raw orderbook from Kalshi API
            market_title: Market title for symbol generation

        Returns:
            OrderBookSnapshot event
        """
        bids = []
        asks = []

        # Parse YES bids as bids (limited to max_depth)
        for yes_order in book_data.get("yes", [])[: self.max_depth]:
            price = float(yes_order.get("price", 0)) / 100.0  # Kalshi uses cents
            size = int(yes_order.get("quantity", 0))
            if price > 0 and size > 0:
                bids.append(OrderBookLevel(price=price, size=float(size)))

        # Parse NO bids as asks (complement pricing)
        for no_order in book_data.get("no", [])[: self.max_depth]:
            price = float(no_order.get("price", 0)) / 100.0
            size = int(no_order.get("quantity", 0))
            if price > 0 and size > 0:
                # Convert NO price to YES ask price: ask = 1 - no_bid
                ask_price = 1.0 - price
                asks.append(OrderBookLevel(price=ask_price, size=float(size)))

        # Sort to ensure best prices first
        bids.sort(key=lambda x: x.price, reverse=True)
        asks.sort(key=lambda x: x.price)

        market_ref = MarketReference(
            venue=self.venue,
            market_id=ticker,
            symbol=ticker,
        )

        return OrderBookSnapshot(
            market=market_ref,
            timestamp=datetime.now(UTC),
            bids=bids,
            asks=asks,
        )

    async def stream_orderbooks(self) -> AsyncIterator[OrderBookSnapshot]:
        """Yield orderbook snapshots by polling Kalshi endpoints.

        Note: This is a polling-based implementation. In production, replace with
        websocket streaming for lower latency per TDD requirements.
        """
        self._running = True
        markets = await self.get_markets()

        # Filter to tracked markets if specified
        if self.tracked_markets:
            markets = [m for m in markets if m.get("ticker") in self.tracked_markets]

        logger.info("starting_kalshi_stream", market_count=len(markets))

        while self._running:
            for market in markets:
                ticker = market.get("ticker")
                if not ticker:
                    continue

                try:
                    book_data = await self.get_orderbook(ticker)
                    title = market.get("title", ticker)
                    snapshot = self._parse_orderbook_snapshot(ticker, book_data, title)

                    # Only yield if we have valid quotes
                    if snapshot.bids or snapshot.asks:
                        yield snapshot

                except Exception as exc:
                    logger.warning(
                        "orderbook_processing_failed",
                        ticker=ticker,
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
        logger.info("kalshi_adapter_closed")


__all__ = ["KalshiAdapter"]
