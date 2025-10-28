"""SQLAlchemy ORM models for the canonical persistence layer."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from arbitrage.domain.markets import Venue


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


class TimestampMixin:
    """Mixin that adds created/updated timestamp columns."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


VENUE_ENUM = Enum(Venue, name="venue_enum")


class Event(Base, TimestampMixin):
    """Canonical event metadata sourced from a venue."""

    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    venue: Mapped[Venue] = mapped_column(VENUE_ENUM, index=True)
    slug_or_ticker: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    markets: Mapped[list["Market"]] = relationship(back_populates="event", lazy="selectin")

    __table_args__ = (
        UniqueConstraint("venue", "slug_or_ticker", name="uq_events_venue_slug"),
    )


class Market(Base, TimestampMixin):
    """Individual binary market listed on a venue."""

    __tablename__ = "markets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    venue: Mapped[Venue] = mapped_column(VENUE_ENUM, index=True)
    ticker_or_token: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    resolution_source: Mapped[str] = mapped_column(String(255), nullable=False)
    close_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    binary_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    event_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("events.id", ondelete="SET NULL"), nullable=True
    )

    event: Mapped[Event | None] = relationship(back_populates="markets", lazy="selectin")
    market_a_pairs: Mapped[list["MarketPair"]] = relationship(
        back_populates="market_a", foreign_keys="MarketPair.market_a_id", lazy="selectin"
    )
    market_b_pairs: Mapped[list["MarketPair"]] = relationship(
        back_populates="market_b", foreign_keys="MarketPair.market_b_id", lazy="selectin"
    )

    __table_args__ = (
        UniqueConstraint("venue", "ticker_or_token", name="uq_markets_venue_ticker"),
        Index("ix_markets_event", "event_id"),
    )


class MarketPair(Base, TimestampMixin):
    """Validated equivalence relationship between two markets."""

    __tablename__ = "market_pairs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    market_a_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("markets.id", ondelete="CASCADE"), nullable=False
    )
    market_b_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("markets.id", ondelete="CASCADE"), nullable=False
    )
    llm_score: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    rules_passed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    active_flag: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    market_a: Mapped[Market] = relationship(
        back_populates="market_a_pairs", foreign_keys=[market_a_id], lazy="selectin"
    )
    market_b: Mapped[Market] = relationship(
        back_populates="market_b_pairs", foreign_keys=[market_b_id], lazy="selectin"
    )
    edges: Mapped[list["Edge"]] = relationship(back_populates="pair", lazy="selectin")

    __table_args__ = (
        UniqueConstraint("market_a_id", "market_b_id", name="uq_market_pairs_unique_pair"),
    )


class OrderbookSnapshot(Base):
    """Depth snapshot for a particular market timestamp."""

    __tablename__ = "orderbooks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("markets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    bid_px: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False)
    bid_sz: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False)
    ask_px: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False)
    ask_sz: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False)
    lvl2_json: Mapped[dict] = mapped_column(JSON, nullable=False)

    market: Mapped[Market] = relationship(lazy="selectin")

    __table_args__ = (
        Index("ix_orderbooks_market_ts", "market_id", "ts"),
    )


class Edge(Base):
    """Computed edge snapshot for a market pair."""

    __tablename__ = "edges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pair_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("market_pairs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    net_edge_cents: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False)
    leader: Mapped[str | None] = mapped_column(String(64), nullable=True)
    signal_conf: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    fee_rev_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    pair: Mapped[MarketPair] = relationship(back_populates="edges", lazy="selectin")

    __table_args__ = (Index("ix_edges_pair_ts", "pair_id", "ts"),)


class Order(Base):
    """Order life-cycle information."""

    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    venue: Mapped[Venue] = mapped_column(VENUE_ENUM, index=True)
    market_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("markets.id", ondelete="CASCADE"), nullable=False
    )
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    px: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False)
    qty: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)
    ts_sent: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ts_ack: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)

    fills: Mapped[list["Fill"]] = relationship(back_populates="order", lazy="selectin", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_orders_market", "market_id"),
    )


class Position(Base, TimestampMixin):
    """Current net position per market on a venue."""

    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    venue: Mapped[Venue] = mapped_column(VENUE_ENUM, index=True)
    market_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("markets.id", ondelete="CASCADE"), nullable=False
    )
    qty_yes: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False, default=0)
    qty_no: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False, default=0)
    avg_px_yes: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    avg_px_no: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)

    market: Mapped[Market] = relationship(lazy="selectin")

    __table_args__ = (
        UniqueConstraint("venue", "market_id", name="uq_positions_venue_market"),
    )


class Fill(Base):
    """Executed fill linked to an order."""

    __tablename__ = "fills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    px: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False)
    qty: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)
    ts_fill: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fee: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    slippage_cents: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)

    order: Mapped[Order] = relationship(back_populates="fills", lazy="selectin")


class ConfigEntry(Base):
    """Versioned configuration store."""

    __tablename__ = "configs"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    val: Mapped[dict] = mapped_column(JSON, nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_configs_key_version", "key", "version"),
    )


__all__ = [
    "Base",
    "Event",
    "Market",
    "MarketPair",
    "OrderbookSnapshot",
    "Edge",
    "Order",
    "Position",
    "Fill",
    "ConfigEntry",
]
