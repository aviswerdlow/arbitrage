"""Kalshi websocket adapter for real-time orderbook streaming."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import AsyncIterator

import websockets
from structlog import get_logger
from tenacity import retry, stop_after_attempt, wait_exponential

from arbitrage.domain.markets import Venue
from arbitrage.events.models import MarketReference, OrderBookLevel, OrderBookSnapshot
from arbitrage.ingest.base import IngestError, VenueAdapter

logger = get_logger(__name__)


class KalshiWebsocketAdapter(VenueAdapter):
    """Kalshi websocket adapter for low-latency orderbook streaming.

    Connects to Kalshi's websocket feed for real-time orderbook updates.
    Implements automatic reconnection and error handling.
    """

    WS_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"
    WS_DEMO_URL = "wss://demo-api.elections.kalshi.com/trade-api/ws/v2"
    API_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
    DEMO_BASE_URL = "https://demo-api.elections.kalshi.com/trade-api/v2"

    def __init__(
        self,
        api_key: str | None = None,
        use_demo: bool = False,
        tracked_markets: list[str] | None = None,
        max_depth: int = 3,
        reconnect_delay: float = 5.0,
    ) -> None:
        """Initialize Kalshi websocket adapter.

        Args:
            api_key: Optional API key for authenticated endpoints
            use_demo: Use demo environment instead of production
            tracked_markets: List of market tickers to track (None = track all binary markets)
            max_depth: Maximum order book depth levels to capture (default 3 per TDD)
            reconnect_delay: Delay between reconnection attempts in seconds
        """
        super().__init__(venue=Venue.KALSHI.value)
        self.api_key = api_key
        self.ws_url = self.WS_DEMO_URL if use_demo else self.WS_URL
        self.base_url = self.DEMO_BASE_URL if use_demo else self.API_BASE_URL
        self.tracked_markets = set(tracked_markets) if tracked_markets else None
        self.max_depth = max_depth
        self.reconnect_delay = reconnect_delay
        self._ws = None
        self._running = False
        self._markets_cache: dict[str, dict] = {}

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True
    )
    async def _fetch_markets(self) -> list[dict]:
        """Fetch market metadata from Kalshi REST API with retry logic.

        Returns:
            List of market dictionaries
        """
        import httpx

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        async with httpx.AsyncClient(headers=headers, timeout=10.0) as client:
            params = {"status": "open", "limit": 200}
            response = await client.get(f"{self.base_url}/markets", params=params)
            response.raise_for_status()
            data = response.json()
            markets = data.get("markets", [])

            # Cache market metadata by ticker
            for market in markets:
                ticker = market.get("ticker")
                if ticker:
                    self._markets_cache[ticker] = market

            logger.info(
                "fetched_kalshi_markets",
                total=len(markets),
                cached=len(self._markets_cache),
            )
            return markets

    def _parse_ws_message(self, message: dict) -> OrderBookSnapshot | None:
        """Parse websocket message into orderbook snapshot.

        Kalshi websocket sends messages in format:
        {
            "type": "orderbook_snapshot" | "orderbook_delta",
            "seq": 123,
            "msg": {
                "market_ticker": "KXELECTION-23NOV-YES",
                "yes": [[55, 100], [54, 200]],  # [price_cents, quantity]
                "no": [[45, 120], [46, 180]]
            }
        }

        Args:
            message: Raw websocket message

        Returns:
            OrderBookSnapshot or None if message can't be parsed
        """
        try:
            msg_type = message.get("type")
            if msg_type not in ("orderbook_snapshot", "orderbook_delta"):
                return None

            msg_data = message.get("msg", {})
            ticker = msg_data.get("market_ticker")

            if not ticker:
                return None

            # Check if we're tracking this market
            if self.tracked_markets and ticker not in self.tracked_markets:
                return None

            # Parse YES bids as bids (buying YES)
            bids = []
            for yes_order in msg_data.get("yes", [])[: self.max_depth]:
                price = float(yes_order[0]) / 100.0  # Kalshi uses cents
                size = int(yes_order[1])
                if price > 0 and size > 0:
                    bids.append(OrderBookLevel(price=price, size=float(size)))

            # Parse NO bids as asks (selling YES / buying NO)
            asks = []
            for no_order in msg_data.get("no", [])[: self.max_depth]:
                price = float(no_order[0]) / 100.0
                size = int(no_order[1])
                if price > 0 and size > 0:
                    # Convert NO price to YES ask price: ask = 1 - no_bid
                    ask_price = 1.0 - price
                    asks.append(OrderBookLevel(price=ask_price, size=float(size)))

            # Sort to ensure best prices first
            bids.sort(key=lambda x: x.price, reverse=True)
            asks.sort(key=lambda x: x.price)

            # Get market info from cache
            market_info = self._markets_cache.get(ticker, {})
            title = market_info.get("title", ticker)

            market_ref = MarketReference(
                venue=self.venue,
                market_id=ticker,
                symbol=ticker,
            )

            return OrderBookSnapshot(
                market=market_ref,
                timestamp=datetime.now(UTC),  # Kalshi doesn't send timestamps in WS
                bids=bids,
                asks=asks,
            )

        except (KeyError, ValueError, TypeError) as exc:
            logger.warning(
                "failed_to_parse_ws_message",
                error=str(exc),
                message_type=message.get("type"),
            )
            return None

    async def _subscribe_to_markets(self, ws, markets: list[dict]) -> None:
        """Subscribe to orderbook updates for tracked markets.

        Args:
            ws: Websocket connection
            markets: List of markets to subscribe to
        """
        # Filter to tracked markets if specified
        if self.tracked_markets:
            markets = [m for m in markets if m.get("ticker") in self.tracked_markets]

        for market in markets:
            ticker = market.get("ticker")
            if not ticker:
                continue

            subscribe_msg = {
                "id": 1,
                "cmd": "subscribe",
                "params": {
                    "channels": ["orderbook_delta"],
                    "market_ticker": ticker,
                },
            }

            await ws.send(json.dumps(subscribe_msg))
            logger.debug("subscribed_to_market", ticker=ticker)

        logger.info("websocket_subscriptions_complete", count=len(markets))

    async def stream_orderbooks(self) -> AsyncIterator[OrderBookSnapshot]:
        """Stream orderbook snapshots via websocket connection.

        Yields:
            OrderBookSnapshot events as they arrive
        """
        self._running = True

        # Fetch markets for metadata
        try:
            markets = await self._fetch_markets()
        except Exception as exc:
            raise IngestError(f"Failed to fetch Kalshi markets: {exc}") from exc

        while self._running:
            try:
                logger.info("connecting_to_kalshi_websocket", url=self.ws_url)

                # Kalshi may require authentication in websocket connection
                headers = {}
                if self.api_key:
                    headers["Authorization"] = f"Bearer {self.api_key}"

                async with websockets.connect(
                    self.ws_url,
                    extra_headers=headers if headers else None,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=10,
                ) as ws:
                    self._ws = ws
                    logger.info("kalshi_websocket_connected")

                    # Subscribe to markets
                    await self._subscribe_to_markets(ws, markets)

                    # Process messages
                    async for message in ws:
                        if not self._running:
                            break

                        try:
                            data = json.loads(message)
                            snapshot = self._parse_ws_message(data)

                            if snapshot and (snapshot.bids or snapshot.asks):
                                yield snapshot

                        except json.JSONDecodeError as exc:
                            logger.warning("invalid_json_message", error=str(exc))
                            continue

            except websockets.exceptions.ConnectionClosed as exc:
                logger.warning(
                    "websocket_connection_closed",
                    code=exc.code,
                    reason=exc.reason,
                )
                if self._running:
                    logger.info(
                        "reconnecting_in_seconds",
                        delay=self.reconnect_delay,
                    )
                    await asyncio.sleep(self.reconnect_delay)

            except Exception as exc:
                logger.error(
                    "websocket_error",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                if self._running:
                    await asyncio.sleep(self.reconnect_delay)
                else:
                    raise IngestError(f"Kalshi websocket failed: {exc}") from exc

    async def close(self) -> None:
        """Clean up websocket connection."""
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None
        logger.info("kalshi_websocket_adapter_closed")


__all__ = ["KalshiWebsocketAdapter"]
