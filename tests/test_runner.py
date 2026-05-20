"""Model-orchestrated runner tests with a fake provider."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

import xframe_agent.models  # noqa: F401
from xframe_agent.agent.runner import ModelRunner
from xframe_agent.auth.jwt import AuthContext
from xframe_agent.db.base import Base
from xframe_agent.models import (
    AgentConversation,
    AgentRun,
    AgentRunEvent,
    AgentRunStep,
    AgentToolCall,
)
from xframe_agent.provider.base import (
    ChatMessage,
    ContentBlock,
    ProviderError,
    ProviderFailoverRouter,
    StreamEvent,
)
from xframe_agent.settings import Settings
from xframe_agent.tools.base import ToolDefinition


class FakeProvider:
    name = "fake"

    def __init__(self, scripts: list[list[StreamEvent]]) -> None:
        self._scripts = scripts
        self._cursor = 0

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDefinition[Any, Any]],
        *,
        model: str,
        max_output_tokens: int,
    ) -> AsyncIterator[StreamEvent]:
        del messages, tools, model, max_output_tokens
        script = self._scripts[self._cursor]
        self._cursor += 1
        for event in script:
            yield event


class FakePriceFrame:
    async def __aenter__(self) -> FakePriceFrame:
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        return None

    async def get_json(self, *_args: object, **_kw: object) -> dict[str, object]:
        return {"id": 123, "name": "PR-1"}


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'runner.db'}",
        priceframe_jwt_secret="x" * 32,
        max_steps_per_run=4,
        max_tool_calls_per_run=4,
        max_input_tokens_per_run=10_000,
        max_output_tokens_per_run=10_000,
    )


def _auth(*permissions: str) -> AuthContext:
    return AuthContext(
        user_id=7,
        role_code="ROLE_AM_SALES",
        profile_code="PROFILE_SALES",
        permissions=permissions or ("agent.enabled", "agent.quotes.read"),
        jwt_raw="jwt",
        session_id=1,
    )


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[tuple[Settings, AsyncEngine, Any, str]]:
    settings = _settings(tmp_path)
    engine = create_async_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        conv = AgentConversation(user_id=7, title="t")
        session.add(conv)
        await session.flush()
        run = AgentRun(conversation_id=conv.id, user_id=7, status="queued")
        session.add(run)
        await session.commit()
        run_id = run.id
    try:
        yield settings, engine, factory, run_id
    finally:
        await engine.dispose()


async def test_runner_executes_read_and_completes(
    db: tuple[Settings, AsyncEngine, Any, str],
) -> None:
    settings, _engine, factory, run_id = db

    provider = FakeProvider(
        [
            [
                StreamEvent(
                    kind="tool_use",
                    payload={"name": "list_my_quotations", "args": {"limit": 5}, "call_id": "c1"},
                ),
                StreamEvent(kind="usage", payload={"input_tokens": 10, "output_tokens": 5}),
            ],
            [
                StreamEvent(kind="text_delta", payload={"delta": "Here you go."}),
                StreamEvent(kind="usage", payload={"input_tokens": 20, "output_tokens": 8}),
            ],
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
            context=_auth("agent.enabled", "agent.quotes.read"),
            history=[
                ChatMessage(
                    role="user",
                    content=[ContentBlock(type="text", payload={"text": "list quotes"})],
                )
            ],
        )

    async with factory() as session:
        run = await session.get(AgentRun, run_id)
        assert run is not None and run.status == "completed"
        tool_calls = (
            (await session.execute(select(AgentToolCall).where(AgentToolCall.run_id == run_id)))
            .scalars()
            .all()
        )
        assert len(tool_calls) == 1
        assert tool_calls[0].tool_name == "list_my_quotations"
        assert tool_calls[0].status == "succeeded"
        steps = (
            (await session.execute(select(AgentRunStep).where(AgentRunStep.run_id == run_id)))
            .scalars()
            .all()
        )
        assert len(steps) >= 3
        events = (
            (await session.execute(select(AgentRunEvent).where(AgentRunEvent.run_id == run_id)))
            .scalars()
            .all()
        )
        kinds = {e.event_type for e in events}
        assert {
            "v1.tool.proposed",
            "v1.tool.started",
            "v1.tool.completed",
            "v1.run.completed",
        } <= kinds


async def test_runner_pauses_on_write_proposal(
    db: tuple[Settings, AsyncEngine, Any, str],
) -> None:
    settings, _engine, factory, run_id = db

    auth = _auth("agent.enabled", "agent.quotes.read", "agent.quotes.edit")
    provider = FakeProvider(
        [
            [
                StreamEvent(
                    kind="tool_use",
                    payload={
                        "name": "set_fx_spread",
                        "args": {
                            "corridor_id": 99,
                            "applied_fx_spread": "0.0200",
                            "minimum_spread": "0.0100",
                        },
                        "call_id": "c1",
                    },
                ),
                StreamEvent(kind="usage", payload={"input_tokens": 10, "output_tokens": 5}),
            ]
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
            context=auth,
            history=[
                ChatMessage(
                    role="user",
                    content=[ContentBlock(type="text", payload={"text": "set fx"})],
                )
            ],
        )

    async with factory() as session:
        run = await session.get(AgentRun, run_id)
        assert run is not None
        assert run.status == "awaiting_decision"
        tool_calls = (
            (await session.execute(select(AgentToolCall).where(AgentToolCall.run_id == run_id)))
            .scalars()
            .all()
        )
        assert len(tool_calls) == 1
        assert tool_calls[0].requires_approval is True
        assert tool_calls[0].status == "proposed"


async def test_runner_aborts_on_loop_detection(
    db: tuple[Settings, AsyncEngine, Any, str],
) -> None:
    settings, _engine, factory, run_id = db

    same = StreamEvent(
        kind="tool_use",
        payload={"name": "list_my_quotations", "args": {"limit": 1}, "call_id": "c"},
    )
    usage = StreamEvent(kind="usage", payload={"input_tokens": 1, "output_tokens": 1})
    provider = FakeProvider([[same, usage], [same, usage], [same, usage]])
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
            context=_auth("agent.enabled", "agent.quotes.read"),
            history=[
                ChatMessage(
                    role="user",
                    content=[ContentBlock(type="text", payload={"text": "x"})],
                )
            ],
        )

    async with factory() as session:
        run = await session.get(AgentRun, run_id)
        assert run is not None and run.status == "error"
        events = (
            (await session.execute(select(AgentRunEvent).where(AgentRunEvent.run_id == run_id)))
            .scalars()
            .all()
        )
        causes = [e.payload.get("cause") for e in events if e.event_type == "v1.run.error"]
        assert "loop_detected" in causes


async def test_runner_surfaces_provider_error(
    db: tuple[Settings, AsyncEngine, Any, str],
) -> None:
    settings, _engine, factory, run_id = db

    raise_flag = True

    async def _broken_stream(*_args: object, **_kwargs: object) -> AsyncIterator[StreamEvent]:
        if raise_flag:
            raise ProviderError("kaboom")
        yield StreamEvent(kind="usage")  # pragma: no cover

    class BrokenProvider:
        name = "broken"
        stream = staticmethod(_broken_stream)

    router = ProviderFailoverRouter(providers=[BrokenProvider()])
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
            context=_auth("agent.enabled", "agent.quotes.read"),
            history=[
                ChatMessage(
                    role="user",
                    content=[ContentBlock(type="text", payload={"text": "x"})],
                )
            ],
        )

    async with factory() as session:
        run = await session.get(AgentRun, run_id)
        assert run is not None and run.status == "error"
