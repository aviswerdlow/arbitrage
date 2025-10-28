"""Integration tests for the canonical persistence layer."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from arbitrage.database.models import Base, Order, OrderbookSnapshot
from arbitrage.database.queries import (
    create_market_pair,
    create_order,
    get_active_pair,
    get_market,
    record_fill,
    record_orderbook_snapshot,
    record_edge,
    upsert_config,
    upsert_event,
    upsert_market,
    upsert_position,
)
from arbitrage.domain.markets import Venue


@pytest.fixture(scope="module")
async def engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(engine) -> AsyncSession:
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
        await session.rollback()


@pytest.mark.asyncio
async def test_market_uniqueness(session: AsyncSession):
    """Markets enforce uniqueness on (venue, ticker_or_token)."""

    await upsert_market(
        session,
        market_id="pm-1",
        venue=Venue.POLYMARKET,
        ticker_or_token="ABC",
        title="Test market",
        resolution_source="source",
        close_time=datetime.now(UTC),
        category="politics",
        binary_flag=True,
        event_id=None,
    )

    with pytest.raises(IntegrityError):
        await upsert_market(
            session,
            market_id="pm-2",
            venue=Venue.POLYMARKET,
            ticker_or_token="ABC",
            title="Duplicate ticker",
            resolution_source="source",
            close_time=datetime.now(UTC),
            category=None,
            binary_flag=True,
            event_id=None,
        )


@pytest.mark.asyncio
async def test_market_pair_creation_and_lookup(session: AsyncSession):
    """Market pairs persist and can be fetched when active."""

    await upsert_market(
        session,
        market_id="pm-a",
        venue=Venue.POLYMARKET,
        ticker_or_token="AAA",
        title="Market A",
        resolution_source="src",
        close_time=datetime.now(UTC),
        category="cat",
        binary_flag=True,
        event_id=None,
    )
    await upsert_market(
        session,
        market_id="kal-b",
        venue=Venue.KALSHI,
        ticker_or_token="BBB",
        title="Market B",
        resolution_source="src",
        close_time=datetime.now(UTC),
        category="cat",
        binary_flag=True,
        event_id=None,
    )

    pair = await create_market_pair(
        session,
        pair_id="pair-1",
        market_a_id="pm-a",
        market_b_id="kal-b",
        llm_score=0.95,
        rules_passed=True,
    )

    fetched = await get_active_pair(session, "pair-1")
    assert fetched is not None
    assert fetched.id == pair.id
    assert fetched.market_a_id == "pm-a"


@pytest.mark.asyncio
async def test_orderbook_and_edge_records(session: AsyncSession):
    """Orderbook snapshots and edges persist with timestamps."""

    market = await upsert_market(
        session,
        market_id="pm-depth",
        venue=Venue.POLYMARKET,
        ticker_or_token="DEPTH",
        title="Depth Market",
        resolution_source="src",
        close_time=datetime.now(UTC),
        category=None,
        binary_flag=True,
        event_id=None,
    )
    hedge_market = await upsert_market(
        session,
        market_id="kal-depth",
        venue=Venue.KALSHI,
        ticker_or_token="DEPTH-H",
        title="Depth Hedge",
        resolution_source="src",
        close_time=datetime.now(UTC),
        category=None,
        binary_flag=True,
        event_id=None,
    )
    ts = datetime.now(UTC)
    snapshot = await record_orderbook_snapshot(
        session,
        market_id=market.id,
        ts=ts,
        bid_px=0.45,
        bid_sz=100,
        ask_px=0.55,
        ask_sz=120,
        lvl2_json={"bids": [[0.45, 100]], "asks": [[0.55, 120]]},
    )
    assert snapshot.id is not None

    await create_market_pair(
        session,
        pair_id="pair-depth",
        market_a_id=market.id,
        market_b_id=hedge_market.id,
        llm_score=0.99,
        rules_passed=True,
    )
    edge = await record_edge(
        session,
        pair_id="pair-depth",
        ts=ts,
        net_edge_cents=3.2,
        leader="polymarket",
        signal_conf=0.8,
        fee_rev_hash="hash",
    )
    assert edge.id is not None

    stmt = select(OrderbookSnapshot).where(OrderbookSnapshot.market_id == market.id)
    result = await session.execute(stmt)
    saved_snapshot = result.scalar_one()
    assert float(saved_snapshot.ask_px) == pytest.approx(0.55)


@pytest.mark.asyncio
async def test_positions_orders_and_fills(session: AsyncSession):
    """Positions, orders, and fills are linked with referential integrity."""

    await upsert_market(
        session,
        market_id="kal-order",
        venue=Venue.KALSHI,
        ticker_or_token="ORDER",
        title="Order Market",
        resolution_source="src",
        close_time=datetime.now(UTC),
        category=None,
        binary_flag=True,
        event_id=None,
    )

    order = await create_order(
        session,
        order_id="kal-o-1",
        venue=Venue.KALSHI,
        market_id="kal-order",
        side="BUY",
        px=0.48,
        qty=25,
        ts_sent=datetime.now(UTC),
        ts_ack=datetime.now(UTC),
        status="acknowledged",
    )

    fill = await record_fill(
        session,
        order_id=order.id,
        px=0.49,
        qty=25,
        ts_fill=datetime.now(UTC),
        fee=0.05,
        slippage_cents=1.0,
    )

    position = await upsert_position(
        session,
        venue=Venue.KALSHI,
        market_id="kal-order",
        qty_yes=25,
        qty_no=0,
        avg_px_yes=0.49,
        avg_px_no=None,
    )

    # Update position
    updated = await upsert_position(
        session,
        venue=Venue.KALSHI,
        market_id="kal-order",
        qty_yes=0,
        qty_no=25,
        avg_px_yes=None,
        avg_px_no=0.51,
    )

    assert position.id == updated.id
    assert float(updated.qty_no) == pytest.approx(25)

    stmt = select(Order).where(Order.id == order.id)
    saved_order = (await session.execute(stmt)).scalar_one()
    assert saved_order.fills[0].id == fill.id


@pytest.mark.asyncio
async def test_config_and_event_merge(session: AsyncSession):
    """Configs and events merge correctly on primary key updates."""

    now = datetime.now(UTC)
    await upsert_event(
        session,
        event_id="event-1",
        venue=Venue.POLYMARKET,
        slug_or_ticker="slug",
        title="Initial",
        start_time=now,
        end_time=now + timedelta(days=1),
    )

    await upsert_event(
        session,
        event_id="event-1",
        venue=Venue.POLYMARKET,
        slug_or_ticker="slug",
        title="Updated",
        start_time=now,
        end_time=now + timedelta(days=2),
    )

    retrieved = await get_market(session, "non-existent")
    assert retrieved is None

    config = await upsert_config(
        session,
        key="thresholds",
        version=1,
        val={"edge": 2.5},
    )
    assert config.val["edge"] == 2.5

