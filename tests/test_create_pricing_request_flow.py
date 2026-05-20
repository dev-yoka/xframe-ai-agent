"""Tests for guided Create Pricing Request flow via ModelRunner."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

import xframe_agent.models  # noqa: F401
from xframe_agent.agent.runner import ModelRunner
from xframe_agent.auth.jwt import AuthContext
from xframe_agent.db.base import Base
from xframe_agent.models import AgentConversation, AgentRun, AgentToolCall
from xframe_agent.provider.base import (
    ChatMessage,
    ContentBlock,
    ProviderFailoverRouter,
    StreamEvent,
)
from xframe_agent.settings import Settings
from xframe_agent.tools.base import ToolDefinition


class FakeProvider:
    """Scriptable provider stub that yields a fixed sequence of StreamEvents."""

    name = "fake"

    def __init__(self, script: list[StreamEvent]) -> None:
        self._script = script
        self.calls: list[list[ChatMessage]] = []

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDefinition[Any, Any]],
        *,
        model: str,
        max_output_tokens: int,
    ) -> AsyncIterator[StreamEvent]:
        self.calls.append(list(messages))
        for event in self._script:
            yield event


class FakePriceFrame:
    async def __aenter__(self) -> FakePriceFrame:
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        return None

    async def get_json(self, *_args: object, **_kw: object) -> dict[str, object]:
        return {"id": 1}


_AUTH = AuthContext(
    user_id=1,
    role_code="ROLE_AM_SALES",
    profile_code="PROFILE_SALES",
    permissions=(
        "agent.enabled",
        "agent.quotes.read",
        "agent.quotes.create",
        "agent.quotes.edit",
        "agent.quotes.recalc",
        "agent.approvals.submit",
    ),
    jwt_raw="test-jwt",
    session_id=1,
)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'cpr.db'}",
        priceframe_jwt_secret="x" * 32,
        max_steps_per_run=10,
        max_tool_calls_per_run=10,
        max_input_tokens_per_run=50_000,
        max_output_tokens_per_run=8_000,
    )


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[tuple[Settings, AsyncEngine, Any, str, str]]:
    settings = _settings(tmp_path)
    engine = create_async_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        conv = AgentConversation(user_id=1, title="CPR Test", kind="create_pricing_request")
        session.add(conv)
        await session.flush()
        run = AgentRun(conversation_id=conv.id, user_id=1, status="queued")
        session.add(run)
        await session.commit()
        conv_id = conv.id
        run_id = run.id
    try:
        yield settings, engine, factory, conv_id, run_id
    finally:
        await engine.dispose()


async def test_system_prompt_injected_for_create_pricing_request_conversation(
    db: tuple[Settings, AsyncEngine, Any, str, str],
) -> None:
    """ModelRunner prepends a system message when conversation.kind='create_pricing_request'."""
    settings, _engine, factory, _conv_id, run_id = db

    provider = FakeProvider(
        script=[
            StreamEvent(kind="text_delta", payload={"delta": "Hello!"}),
            StreamEvent(kind="usage", payload={"input_tokens": 5, "output_tokens": 3}),
        ]
    )
    router = ProviderFailoverRouter(providers=[provider])
    runner = ModelRunner(
        router=router,
        settings=settings,
        model="gemini-2.5-flash",
        priceframe_factory=FakePriceFrame(),  # type: ignore[arg-type]
    )

    async with factory() as session:
        run = await session.get(AgentRun, run_id)
        assert run is not None
        await runner.run(
            session,
            run=run,
            context=_AUTH,
            history=[
                ChatMessage(
                    role="user",
                    content=[ContentBlock(type="text", payload={"text": "create pricing request"})],
                )
            ],
        )

    assert len(provider.calls) == 1
    messages_sent = provider.calls[0]
    assert messages_sent[0].role == "system"
    system_text: str = messages_sent[0].content[0].payload["text"]
    assert "xFRAME AI Agent" in system_text
    assert "pricing assistant for PriceFRAME" in system_text
    assert messages_sent[1].role == "user"


async def test_create_pricing_request_flow_pauses_on_create_quotation(
    db: tuple[Settings, AsyncEngine, Any, str, str],
) -> None:
    """A tool_use event for create_quotation puts the run into awaiting_decision."""
    settings, _engine, factory, _conv_id, run_id = db

    provider = FakeProvider(
        script=[
            StreamEvent(
                kind="tool_use",
                payload={
                    "name": "create_quotation",
                    "call_id": "c1",
                    "args": {"title": "Test Q", "customer_id": 1, "currency": "USD"},
                },
            ),
            StreamEvent(kind="usage", payload={"input_tokens": 10, "output_tokens": 5}),
        ]
    )
    router = ProviderFailoverRouter(providers=[provider])
    runner = ModelRunner(
        router=router,
        settings=settings,
        model="gemini-2.5-flash",
        priceframe_factory=FakePriceFrame(),  # type: ignore[arg-type]
    )

    async with factory() as session:
        run = await session.get(AgentRun, run_id)
        assert run is not None
        result = await runner.run(
            session,
            run=run,
            context=_AUTH,
            history=[
                ChatMessage(
                    role="user",
                    content=[ContentBlock(type="text", payload={"text": "create pricing request"})],
                )
            ],
        )

    assert result.status == "awaiting_decision"

    async with factory() as session:
        tool_calls = (
            (await session.execute(select(AgentToolCall).where(AgentToolCall.run_id == run_id)))
            .scalars()
            .all()
        )
    assert len(tool_calls) == 1
    assert tool_calls[0].tool_name == "create_quotation"
    assert tool_calls[0].requires_approval is True
    assert tool_calls[0].status == "proposed"
