"""Tests for ``agent.dispatch.execute_run`` routing logic."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

import xframe_agent.models  # noqa: F401
from xframe_agent.agent import dispatch
from xframe_agent.agent.dispatch import execute_run
from xframe_agent.auth.jwt import AuthContext
from xframe_agent.db.base import Base
from xframe_agent.models import AgentConversation, AgentMessage, AgentRun
from xframe_agent.priceframe import PriceFrameClient
from xframe_agent.provider.base import (
    ChatMessage,
    ProviderFailoverRouter,
    StreamEvent,
)
from xframe_agent.settings import Settings

_AUTH = AuthContext(
    user_id=1,
    role_code="ROLE_AM_SALES",
    profile_code="PROFILE_SALES",
    permissions=("agent.enabled", "agent.quotes.read"),
    jwt_raw="jwt-for-tests",
    session_id=1,
)


class FakeProvider:
    name = "fake"

    def __init__(self, script: list[StreamEvent]) -> None:
        self._script = script
        self.calls: list[list[ChatMessage]] = []

    async def stream(
        self,
        messages: Any,
        tools: Any,
        *,
        model: str,
        max_output_tokens: int,
    ) -> AsyncIterator[StreamEvent]:
        self.calls.append(list(messages))
        for event in self._script:
            yield event


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'dispatch.db'}",
        priceframe_jwt_secret="x" * 32,
        gemini_api_key=None,
        gemini_aistudio_api_key=None,
        gemini_vertex_project=None,
        anthropic_api_key=None,
    )


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[tuple[Settings, AsyncEngine, Any, str, str]]:
    settings = _settings(tmp_path)
    engine = create_async_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        conv = AgentConversation(user_id=1, title="dispatch test", kind="general")
        session.add(conv)
        await session.flush()
        message = AgentMessage(
            conversation_id=conv.id,
            user_id=1,
            role="user",
            content="hello",
            source="text",
        )
        session.add(message)
        await session.flush()
        run = AgentRun(
            conversation_id=conv.id,
            user_id=1,
            status="queued",
            input_message_id=message.id,
        )
        session.add(run)
        await session.commit()
        conv_id = conv.id
        run_id = run.id
    try:
        yield settings, engine, factory, conv_id, run_id
    finally:
        await engine.dispose()


async def test_execute_run_uses_agentloop_when_no_provider_configured(
    db: tuple[Settings, AsyncEngine, Any, str, str],
) -> None:
    """No provider env vars → falls back to AgentLoop."""
    settings, _engine, factory, _conv_id, run_id = db
    assert not settings.provider_configured

    async with factory() as session:
        run = await execute_run(session, settings=settings, run_id=run_id, context=_AUTH)
        # AgentLoop reaches "completed" because the input message is present and
        # there's no tool directive in it.
        assert run.id == run_id
        assert run.status == "completed"


async def test_execute_run_uses_modelrunner_when_provider_configured(
    monkeypatch: pytest.MonkeyPatch,
    db: tuple[Settings, AsyncEngine, Any, str, str],
) -> None:
    """Provider configured → uses ModelRunner with a built router + history."""
    settings, _engine, factory, _conv_id, run_id = db
    settings_with_provider = settings.model_copy(update={"gemini_vertex_project": "test-proj"})

    provider = FakeProvider(
        script=[
            StreamEvent(kind="text_delta", payload={"delta": "ack"}),
            StreamEvent(kind="usage", payload={"input_tokens": 1, "output_tokens": 1}),
        ]
    )

    def fake_build_router(_settings: Settings) -> ProviderFailoverRouter:
        return ProviderFailoverRouter(providers=[provider])

    monkeypatch.setattr(dispatch, "build_router", fake_build_router)

    class FakePriceFrame:
        async def __aenter__(self) -> FakePriceFrame:
            return self

        async def __aexit__(self, *_exc_info: object) -> None:
            return None

        async def get_json(self, *_args: object, **_kw: object) -> dict[str, object]:
            return {}

    def fake_from_settings(_settings: Settings) -> FakePriceFrame:
        return FakePriceFrame()

    monkeypatch.setattr(PriceFrameClient, "from_settings", staticmethod(fake_from_settings))

    async with factory() as session:
        run = await execute_run(
            session, settings=settings_with_provider, run_id=run_id, context=_AUTH
        )
        assert run.status == "completed"

    # ModelRunner was actually called — the FakeProvider captured the messages.
    assert len(provider.calls) == 1
