"""Polymarket websocket adapter for real-time orderbook streaming."""

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


class PolymarketWebsocketAdapter(VenueAdapter):
    """Polymarket websocket adapter for low-latency orderbook streaming.

    Connects to Polymarket's websocket feed for real-time orderbook updates.
    Implements automatic reconnection and error handling.
    """

    WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    REST_BASE_URL = "https://clob.polymarket.com"
    GAMMA_BASE_URL = "https://gamma-api.polymarket.com"

    def __init__(
        self,
        api_key: str | None = None,
        tracked_markets: list[str] | None = None,
        max_depth: int = 3,
        reconnect_delay: float = 5.0,
    ) -> None:
        """Initialize Polymarket websocket adapter.

        Args:
            api_key: Optional API key for authenticated endpoints
            tracked_markets: List of token IDs to track (None = track all binary markets)
            max_depth: Maximum order book depth levels to capture (default 3 per TDD)
            reconnect_delay: Delay between reconnection attempts in seconds
        """
        super().__init__(venue=Venue.POLYMARKET.value)
        self.api_key = api_key
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
        """Fetch market metadata from Gamma API with retry logic.

        Returns:
            List of market dictionaries
        """
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{self.GAMMA_BASE_URL}/markets")
            response.raise_for_status()
            markets = response.json()

            # Filter for binary markets only
            binary_markets = [m for m in markets if m.get("enableOrderBook", False)]

            # Cache market metadata by token ID
            for market in binary_markets:
                token_id = market.get("tokenID")
                if token_id:
                    self._markets_cache[token_id] = market

            logger.info(
                "fetched_polymarket_markets",
                total=len(markets),
                binary=len(binary_markets),
                cached=len(self._markets_cache),
            )
            return binary_markets

    def _parse_ws_message(self, message: dict) -> OrderBookSnapshot | None:
        """Parse websocket message into orderbook snapshot.

        Polymarket websocket sends messages in format:
        {
            "event_type": "book",
            "market": "0x123...",
            "timestamp": 1234567890,
            "book": {
                "bids": [["0.55", "100"], ...],
                "asks": [["0.56", "120"], ...]
            }
        }

        Args:
            message: Raw websocket message

        Returns:
            OrderBookSnapshot or None if message can't be parsed
        """
        try:
            event_type = message.get("event_type")
            if event_type != "book":
                return None

            token_id = message.get("market")
            if not token_id:
                return None

            # Check if we're tracking this market
            if self.tracked_markets and token_id not in self.tracked_markets:
                return None

            book_data = message.get("book", {})

            bids = []
            for bid in book_data.get("bids", [])[: self.max_depth]:
                price = float(bid[0])
                size = float(bid[1])
                if price > 0 and size > 0:
                    bids.append(OrderBookLevel(price=price, size=size))

            asks = []
            for ask in book_data.get("asks", [])[: self.max_depth]:
                price = float(ask[0])
                size = float(ask[1])
                if price > 0 and size > 0:
                    asks.append(OrderBookLevel(price=price, size=size))

            # Get market symbol from cache
            market_info = self._markets_cache.get(token_id, {})
            symbol = market_info.get("ticker", token_id)

            market_ref = MarketReference(
                venue=self.venue,
                market_id=token_id,
                symbol=symbol,
            )

            return OrderBookSnapshot(
                market=market_ref,
                timestamp=datetime.fromtimestamp(
                    message.get("timestamp", datetime.now().timestamp()),
                    tz=UTC
                ),
                bids=bids,
                asks=asks,
            )

        except (KeyError, ValueError, TypeError) as exc:
            logger.warning(
                "failed_to_parse_ws_message",
                error=str(exc),
                message_type=message.get("event_type"),
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
            markets = [m for m in markets if m.get("tokenID") in self.tracked_markets]

        for market in markets:
            token_id = market.get("tokenID")
            if not token_id:
                continue

            subscribe_msg = {
                "type": "subscribe",
                "channel": "book",
                "market": token_id,
            }

            await ws.send(json.dumps(subscribe_msg))
            logger.debug("subscribed_to_market", token_id=token_id)

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
            raise IngestError(f"Failed to fetch Polymarket markets: {exc}") from exc

        while self._running:
            try:
                logger.info("connecting_to_polymarket_websocket", url=self.WS_URL)

                async with websockets.connect(
                    self.WS_URL,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=10,
                ) as ws:
                    self._ws = ws
                    logger.info("polymarket_websocket_connected")

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
                    raise IngestError(f"Polymarket websocket failed: {exc}") from exc

    async def close(self) -> None:
        """Clean up websocket connection."""
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None
        logger.info("polymarket_websocket_adapter_closed")


__all__ = ["PolymarketWebsocketAdapter"]
