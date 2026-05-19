"""Async SQLAlchemy engine, session factory, and health probe."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from xframe_agent.settings import Settings


def make_engine(settings: Settings) -> AsyncEngine:
    """Create an async SQLAlchemy engine."""

    return create_async_engine(settings.database_url, pool_pre_ping=True)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create an async session factory for request or worker scopes."""

    return async_sessionmaker(engine, expire_on_commit=False)


async def session_scope(settings: Settings) -> AsyncIterator[AsyncSession]:
    """Yield an async session and dispose the short-lived engine afterwards."""

    engine = make_engine(settings)
    session_factory = make_session_factory(engine)
    try:
        async with session_factory() as session:
            yield session
    finally:
        await engine.dispose()


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield a request-scoped async database session."""

    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        yield session


async def check_database(settings: Settings) -> None:
    """Run a minimal database connectivity check."""

    engine = make_engine(settings)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    finally:
        await engine.dispose()
