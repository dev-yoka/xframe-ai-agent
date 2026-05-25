"""Tests for the daily arq cleanup job that purges expired workflow drafts."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import xframe_agent.models  # noqa: F401 — registers ORM metadata
from xframe_agent.db.base import Base
from xframe_agent.db.session import make_session_factory
from xframe_agent.models import AgentConversation
from xframe_agent.models.agent import AgentWorkflowDraft, utc_now
from xframe_agent.settings import Settings
from xframe_agent.worker import purge_expired_workflow_drafts

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_engine(tmp_path: Path) -> AsyncEngine:  # type: ignore[misc]
    """Yield a fresh in-memory-like SQLite engine with the full schema."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'drafts_cleanup.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session_factory(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return make_session_factory(db_engine)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _insert_conversation(
    session_factory: async_sessionmaker[AsyncSession], *, conv_id: str
) -> None:
    """Insert a minimal AgentConversation row so FK constraints pass."""
    async with session_factory() as session:
        now = utc_now()
        conv = AgentConversation(
            id=conv_id,
            user_id=1,
            title="Test conversation",
            created_at=now,
            updated_at=now,
        )
        session.add(conv)
        await session.commit()


async def _insert_draft(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    conversation_id: str,
    expires_at: datetime,
) -> None:
    """Insert an AgentWorkflowDraft with the given expiry."""
    async with session_factory() as session:
        now = utc_now()
        draft = AgentWorkflowDraft(
            conversation_id=conversation_id,
            contract_id="create_pricing_request",
            contract_version="v1",
            current_step_id="step_1",
            payload={},
            step_status={},
            created_at=now,
            updated_at=now,
            expires_at=expires_at,
        )
        session.add(draft)
        await session.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_purge_deletes_expired_draft_and_keeps_future_draft(
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Exactly the expired row is deleted; the future-dated draft survives."""

    # Patch Settings so the job connects to the same test DB.
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'drafts_cleanup.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setattr(
        "xframe_agent.worker.Settings",
        lambda: Settings(
            database_url=database_url,
            priceframe_jwt_secret="x" * 32,
            gemini_api_key=None,
            gemini_aistudio_api_key=None,
            gemini_vertex_project=None,
            anthropic_api_key=None,
        ),
    )

    expired_conv_id = "conv_expired_000001"
    future_conv_id = "conv_future_0000001"

    # Insert supporting conversations first (FK parent).
    await _insert_conversation(session_factory, conv_id=expired_conv_id)
    await _insert_conversation(session_factory, conv_id=future_conv_id)

    # Insert one already-expired draft and one that expires in 7 days.
    now = utc_now()
    await _insert_draft(
        session_factory,
        conversation_id=expired_conv_id,
        expires_at=now - timedelta(hours=1),
    )
    await _insert_draft(
        session_factory,
        conversation_id=future_conv_id,
        expires_at=now + timedelta(days=7),
    )

    # Run the cleanup job (pass empty ctx dict — mirrors arq's real context).
    deleted_count = await purge_expired_workflow_drafts({})

    assert deleted_count == 1, f"Expected 1 deleted row, got {deleted_count}"

    # Verify only the future draft remains in the DB.
    async with session_factory() as session:
        remaining = (await session.execute(select(AgentWorkflowDraft))).scalars().all()

    assert len(remaining) == 1
    assert remaining[0].conversation_id == future_conv_id


async def test_purge_returns_zero_when_no_drafts_are_expired(
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Returns 0 and leaves the table intact when nothing has expired yet."""

    database_url = f"sqlite+aiosqlite:///{tmp_path / 'drafts_cleanup.db'}"
    monkeypatch.setattr(
        "xframe_agent.worker.Settings",
        lambda: Settings(
            database_url=database_url,
            priceframe_jwt_secret="x" * 32,
            gemini_api_key=None,
            gemini_aistudio_api_key=None,
            gemini_vertex_project=None,
            anthropic_api_key=None,
        ),
    )

    conv_id = "conv_future_only_001"
    await _insert_conversation(session_factory, conv_id=conv_id)
    await _insert_draft(
        session_factory,
        conversation_id=conv_id,
        expires_at=utc_now() + timedelta(days=3),
    )

    deleted_count = await purge_expired_workflow_drafts({})

    assert deleted_count == 0

    async with session_factory() as session:
        remaining = (await session.execute(select(AgentWorkflowDraft))).scalars().all()
    assert len(remaining) == 1
