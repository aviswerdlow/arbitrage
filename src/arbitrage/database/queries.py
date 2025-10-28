"""High-level async helpers for interacting with the persistence layer."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arbitrage.domain.markets import Venue

from .models import (
    ConfigEntry,
    Edge,
    Event,
    Fill,
    Market,
    MarketPair,
    Order,
    OrderbookSnapshot,
    Position,
)


async def upsert_event(
    session: AsyncSession,
    *,
    event_id: str,
    venue: Venue,
    slug_or_ticker: str,
    title: str,
    start_time: datetime,
    end_time: datetime | None,
) -> Event:
    """Insert or update an event record."""

    event = Event(
        id=event_id,
        venue=venue,
        slug_or_ticker=slug_or_ticker,
        title=title,
        start_time=start_time,
        end_time=end_time,
    )
    persisted = await session.merge(event)
    await session.flush()
    return persisted


async def upsert_market(
    session: AsyncSession,
    *,
    market_id: str,
    venue: Venue,
    ticker_or_token: str,
    title: str,
    resolution_source: str,
    close_time: datetime,
    category: str | None,
    binary_flag: bool,
    event_id: str | None,
) -> Market:
    """Insert or update a market record."""

    market = Market(
        id=market_id,
        venue=venue,
        ticker_or_token=ticker_or_token,
        title=title,
        resolution_source=resolution_source,
        close_time=close_time,
        category=category,
        binary_flag=binary_flag,
        event_id=event_id,
    )
    persisted = await session.merge(market)
    await session.flush()
    return persisted


async def create_market_pair(
    session: AsyncSession,
    *,
    pair_id: str,
    market_a_id: str,
    market_b_id: str,
    llm_score: float,
    rules_passed: bool,
    active_flag: bool = True,
) -> MarketPair:
    """Persist a validated market pair."""

    pair = MarketPair(
        id=pair_id,
        market_a_id=market_a_id,
        market_b_id=market_b_id,
        llm_score=llm_score,
        rules_passed=rules_passed,
        active_flag=active_flag,
    )
    session.add(pair)
    await session.flush()
    return pair


async def record_orderbook_snapshot(
    session: AsyncSession,
    *,
    market_id: str,
    ts: datetime,
    bid_px: float,
    bid_sz: float,
    ask_px: float,
    ask_sz: float,
    lvl2_json: dict,
) -> OrderbookSnapshot:
    """Store a depth snapshot for a market."""

    snapshot = OrderbookSnapshot(
        market_id=market_id,
        ts=ts,
        bid_px=bid_px,
        bid_sz=bid_sz,
        ask_px=ask_px,
        ask_sz=ask_sz,
        lvl2_json=lvl2_json,
    )
    session.add(snapshot)
    await session.flush()
    return snapshot


async def record_edge(
    session: AsyncSession,
    *,
    pair_id: str,
    ts: datetime,
    net_edge_cents: float,
    leader: str | None,
    signal_conf: float | None,
    fee_rev_hash: str | None,
) -> Edge:
    """Insert an edge measurement for a market pair."""

    edge = Edge(
        pair_id=pair_id,
        ts=ts,
        net_edge_cents=net_edge_cents,
        leader=leader,
        signal_conf=signal_conf,
        fee_rev_hash=fee_rev_hash,
    )
    session.add(edge)
    await session.flush()
    return edge


async def upsert_position(
    session: AsyncSession,
    *,
    venue: Venue,
    market_id: str,
    qty_yes: float,
    qty_no: float,
    avg_px_yes: float | None,
    avg_px_no: float | None,
) -> Position:
    """Insert or update a venue position."""

    stmt = select(Position).where(
        Position.venue == venue,
        Position.market_id == market_id,
    )
    result = await session.execute(stmt)
    position = result.scalar_one_or_none()
    if position is None:
        position = Position(
            venue=venue,
            market_id=market_id,
            qty_yes=qty_yes,
            qty_no=qty_no,
            avg_px_yes=avg_px_yes,
            avg_px_no=avg_px_no,
        )
        session.add(position)
    else:
        position.qty_yes = qty_yes
        position.qty_no = qty_no
        position.avg_px_yes = avg_px_yes
        position.avg_px_no = avg_px_no
    await session.flush()
    return position


async def create_order(
    session: AsyncSession,
    *,
    order_id: str,
    venue: Venue,
    market_id: str,
    side: str,
    px: float,
    qty: float,
    ts_sent: datetime,
    ts_ack: datetime | None,
    status: str,
) -> Order:
    """Persist a new order record."""

    order = Order(
        id=order_id,
        venue=venue,
        market_id=market_id,
        side=side,
        px=px,
        qty=qty,
        ts_sent=ts_sent,
        ts_ack=ts_ack,
        status=status,
    )
    session.add(order)
    await session.flush()
    return order


async def record_fill(
    session: AsyncSession,
    *,
    order_id: str,
    px: float,
    qty: float,
    ts_fill: datetime,
    fee: float | None,
    slippage_cents: float | None,
) -> Fill:
    """Persist a fill linked to an order."""

    fill = Fill(
        order_id=order_id,
        px=px,
        qty=qty,
        ts_fill=ts_fill,
        fee=fee,
        slippage_cents=slippage_cents,
    )
    session.add(fill)
    await session.flush()
    return fill


async def upsert_config(
    session: AsyncSession,
    *,
    key: str,
    version: int,
    val: dict,
) -> ConfigEntry:
    """Create or update a configuration entry."""

    entry = ConfigEntry(key=key, version=version, val=val)
    persisted = await session.merge(entry)
    await session.flush()
    return persisted


async def get_market(session: AsyncSession, market_id: str) -> Market | None:
    """Return a market by its identifier."""

    stmt = select(Market).where(Market.id == market_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_active_pair(session: AsyncSession, pair_id: str) -> MarketPair | None:
    """Return an active market pair if it exists."""

    stmt = select(MarketPair).where(
        MarketPair.id == pair_id,
        MarketPair.active_flag.is_(True),
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


__all__ = [
    "upsert_event",
    "upsert_market",
    "create_market_pair",
    "record_orderbook_snapshot",
    "record_edge",
    "upsert_position",
    "create_order",
    "record_fill",
    "upsert_config",
    "get_market",
    "get_active_pair",
]
