"""Ingestion service abstractions for venue data feeds."""

from __future__ import annotations

import abc
import asyncio
from dataclasses import dataclass
from typing import AsyncIterator, Awaitable, Callable

from arbitrage.events.models import EventType, OrderBookSnapshot


class IngestError(RuntimeError):
    """Raised when an ingest adapter cannot continue streaming."""


class VenueAdapter(abc.ABC):
    """Abstract base class for all venue-specific adapters."""

    venue: str

    def __init__(self, venue: str) -> None:
        self.venue = venue

    @abc.abstractmethod
    async def stream_orderbooks(self) -> AsyncIterator[OrderBookSnapshot]:
        """Yield order book snapshots as they arrive from the venue."""

    async def run(
        self, handler: Callable[[EventType, OrderBookSnapshot], Awaitable[None]]
    ) -> None:
        """Continuously stream events and feed them into the provided handler."""

        try:
            async for snapshot in self.stream_orderbooks():
                await handler(EventType.ORDERBOOK_UPDATE, snapshot)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - safety net for adapters
            raise IngestError(f"{self.venue} adapter failed: {exc}") from exc


@dataclass
class IngestService:
    """Coordinates venue adapters and forwards their output downstream."""

    adapters: list[VenueAdapter]

    async def run(
        self, handler: Callable[[EventType, OrderBookSnapshot], Awaitable[None]]
    ) -> None:
        """Start all adapters concurrently and wait until the first failure."""

        async def _wrap(adapter: VenueAdapter) -> None:
            await adapter.run(handler)

        await asyncio.gather(*[_wrap(adapter) for adapter in self.adapters])


__all__ = ["IngestError", "IngestService", "VenueAdapter"]
