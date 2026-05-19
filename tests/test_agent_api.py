"""Phase D conversation, run, SSE, and tool discovery tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine

import xframe_agent.models  # noqa: F401
from xframe_agent.auth.dependencies import get_auth_context
from xframe_agent.auth.jwt import AuthContext
from xframe_agent.db.base import Base
from xframe_agent.main import create_app
from xframe_agent.settings import Settings


@pytest.fixture
async def agent_client(test_settings: Settings, tmp_path: Path) -> AsyncIterator[AsyncClient]:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'agent.db'}"
    settings = test_settings.model_copy(
        update={
            "database_url": database_url,
            "run_execution_mode": "inline",
            "sse_redis_buffer_enabled": False,
        }
    )
    app = create_app(settings)

    async def fake_auth_context() -> AuthContext:
        return AuthContext(
            user_id=7,
            role_code="ROLE_AM_SALES",
            profile_code="PROFILE_SALES",
            permissions=(
                "agent.enabled",
                "agent.quotes.read",
                "agent.quotes.recalc",
                "agent.salesforce.read",
            ),
            jwt_raw="jwt-for-tests",
            session_id=42,
        )

    app.dependency_overrides[get_auth_context] = fake_auth_context

    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
    await app.state.engine.dispose()


async def test_conversation_run_and_sse_replay(agent_client: AsyncClient) -> None:
    create_response = await agent_client.post(
        "/api/v1/agent/conversations",
        headers={"Idempotency-Key": "create-conversation-1"},
        json={"title": "Pricing request"},
    )
    assert create_response.status_code == 201
    conversation = create_response.json()

    replay_response = await agent_client.post(
        "/api/v1/agent/conversations",
        headers={"Idempotency-Key": "create-conversation-1"},
        json={"title": "Pricing request"},
    )
    assert replay_response.status_code == 200
    assert replay_response.headers["Idempotency-Replayed"] == "true"
    assert replay_response.json()["id"] == conversation["id"]

    run_response = await agent_client.post(
        f"/api/v1/agent/conversations/{conversation['id']}/runs",
        headers={"Idempotency-Key": "run-1"},
        json={"content": "Summarize quotation 123"},
    )
    assert run_response.status_code == 202
    run_id = run_response.json()["run_id"]

    run_snapshot = await agent_client.get(f"/api/v1/agent/runs/{run_id}")
    assert run_snapshot.status_code == 200
    assert run_snapshot.json()["status"] == "completed"

    stream_response = await agent_client.get(f"/api/v1/agent/runs/{run_id}/stream")
    assert stream_response.status_code == 200
    stream_text = stream_response.text
    assert "event: v1.run.started" in stream_text
    assert "event: v1.message.delta" in stream_text
    assert "event: v1.run.completed" in stream_text


async def test_tool_discovery_filters_by_permissions(agent_client: AsyncClient) -> None:
    response = await agent_client.get("/api/v1/agent/tools")

    assert response.status_code == 200
    tool_names = {tool["name"] for tool in response.json()["tools"]}
    assert "get_quotation" in tool_names
    assert "recalculate_quote_aggregates" in tool_names
    assert "create_quotation" not in tool_names
