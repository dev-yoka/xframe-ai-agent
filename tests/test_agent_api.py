"""Phase D conversation, run, SSE, and tool discovery tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sse_starlette.sse import AppStatus

import xframe_agent.models  # noqa: F401
from xframe_agent.api.v1 import conversations
from xframe_agent.auth.dependencies import get_auth_context
from xframe_agent.auth.jwt import AuthContext
from xframe_agent.db.base import Base
from xframe_agent.main import create_app
from xframe_agent.models import AgentRun
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
    AppStatus.should_exit = False
    AppStatus.should_exit_event = None
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


async def test_send_message_returns_actual_run_status(
    agent_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_execute_run(
        session: AsyncSession,
        *,
        settings: Settings,
        run_id: str,
        context: AuthContext,
    ) -> AgentRun:
        del settings, context
        run = await session.get(AgentRun, run_id)
        assert run is not None
        run.status = "error"
        run.error = "provider unavailable"
        return run

    monkeypatch.setattr(conversations, "execute_run", fake_execute_run)
    create_response = await agent_client.post(
        "/api/v1/agent/conversations",
        headers={"Idempotency-Key": "send-message-conversation"},
        json={"title": "Message status"},
    )
    assert create_response.status_code == 201
    conversation_id = create_response.json()["id"]

    message_response = await agent_client.post(
        f"/api/v1/agent/conversations/{conversation_id}/messages",
        headers={"Idempotency-Key": "send-message-error"},
        json={"content": "Show me my open quotations", "source": "text"},
    )

    assert message_response.status_code == 200
    assert message_response.json()["status"] == "error"


async def test_workflow_draft_create_get_delete(agent_client: AsyncClient) -> None:
    create_response = await agent_client.post(
        "/api/v1/agent/conversations",
        headers={"Idempotency-Key": "draft-conversation"},
        json={"title": "Draft workflow", "kind": "create_pricing_request"},
    )
    assert create_response.status_code == 201
    conversation_id = create_response.json()["id"]

    missing_response = await agent_client.get(
        f"/api/v1/agent/conversations/{conversation_id}/draft"
    )
    assert missing_response.status_code == 404

    save_response = await agent_client.post(
        f"/api/v1/agent/conversations/{conversation_id}/draft",
        json={
            "contract_id": "create_pricing_request",
            "contract_version": "v1",
            "current_step_id": "summary",
            "payload": {"summary": {"opportunity_name": "MEA expansion"}},
            "step_status": {"summary": "active", "setup_fee": "pending"},
        },
    )
    assert save_response.status_code == 200
    saved = save_response.json()
    assert saved["conversation_id"] == conversation_id
    assert saved["payload"]["summary"]["opportunity_name"] == "MEA expansion"
    assert saved["step_status"]["summary"] == "active"
    assert saved["expires_at"] is not None

    get_response = await agent_client.get(f"/api/v1/agent/conversations/{conversation_id}/draft")
    assert get_response.status_code == 200
    assert get_response.json()["payload"] == saved["payload"]

    delete_response = await agent_client.delete(
        f"/api/v1/agent/conversations/{conversation_id}/draft"
    )
    assert delete_response.status_code == 204

    deleted_response = await agent_client.get(
        f"/api/v1/agent/conversations/{conversation_id}/draft"
    )
    assert deleted_response.status_code == 404


async def test_create_pricing_request_emits_workflow_step_event(agent_client: AsyncClient) -> None:
    create_response = await agent_client.post(
        "/api/v1/agent/conversations",
        headers={"Idempotency-Key": "workflow-step-conversation"},
        json={"title": "Wizard", "kind": "create_pricing_request"},
    )
    assert create_response.status_code == 201
    conversation_id = create_response.json()["id"]

    run_response = await agent_client.post(
        f"/api/v1/agent/conversations/{conversation_id}/runs",
        headers={"Idempotency-Key": "workflow-step-run"},
        json={"content": "Create a pricing request", "source": "text"},
    )
    assert run_response.status_code == 202
    run_id = run_response.json()["run_id"]

    stream_response = await agent_client.get(f"/api/v1/agent/runs/{run_id}/stream")
    assert stream_response.status_code == 200
    stream_text = stream_response.text
    assert "event: v1.workflow.step.entered" in stream_text
    assert '"contract_id":"create_pricing_request"' in stream_text
    assert '"contract_version":"v1"' in stream_text
    assert '"step_id":"summary"' in stream_text
    assert '"step_index":0' in stream_text
    assert '"total_steps":7' in stream_text
    assert "event: v1.input.requested" in stream_text
