"""Async database session management."""

from __future__ import annotations

from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from arbitrage.config import get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Return a cached async SQLAlchemy engine."""

    global _engine, _session_factory
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(settings.database_url, echo=False, future=True)
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def async_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return cached session factory."""

    global _session_factory
    if _session_factory is None:
        get_engine()
    assert _session_factory is not None
    return _session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    """Dependency for FastAPI routes."""

    session_factory = async_session_factory()
    async with session_factory() as session:
        yield session
