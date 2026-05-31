"""Endpoint tests for the conversational field-answer + commit routes."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine
from sse_starlette.sse import AppStatus

import xframe_agent.models  # noqa: F401
from xframe_agent.agent.events import list_run_events
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
                "agent.quotes.create",
                "agent.quotes.recalc",
                "agent.quotes.edit",
                "agent.approvals.submit",
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


async def _start_wizard(agent_client: AsyncClient) -> tuple[str, str]:
    create_response = await agent_client.post(
        "/api/v1/agent/conversations",
        headers={"Idempotency-Key": "field-answer-conversation"},
        json={"title": "Wizard", "kind": "create_pricing_request"},
    )
    assert create_response.status_code == 201
    conversation_id = create_response.json()["id"]
    run_response = await agent_client.post(
        f"/api/v1/agent/conversations/{conversation_id}/runs",
        headers={"Idempotency-Key": "field-answer-run"},
        json={"content": "Create a pricing request", "source": "text"},
    )
    assert run_response.status_code == 202
    return conversation_id, run_response.json()["run_id"]


async def _seed_draft(
    agent_client: AsyncClient,
    conversation_id: str,
    payload: dict[str, Any],
    current_step_id: str = "summary",
) -> None:
    save = await agent_client.post(
        f"/api/v1/agent/conversations/{conversation_id}/draft",
        json={
            "contract_id": "create_pricing_request",
            "contract_version": "v1",
            "current_step_id": current_step_id,
            "payload": payload,
            "step_status": {},
        },
    )
    assert save.status_code == 200


async def _run_event_types(agent_client: AsyncClient, run_id: str) -> list[str]:
    session_factory = agent_client._transport.app.state.session_factory  # type: ignore[attr-defined]
    async with session_factory() as session:
        events = await list_run_events(session, run_id=run_id)
    return [event.event_type for event in events]


async def test_field_answer_valid_returns_next_prompt(agent_client: AsyncClient) -> None:
    conversation_id, run_id = await _start_wizard(agent_client)
    await _seed_draft(agent_client, conversation_id, {"summary": {}})

    response = await agent_client.post(
        f"/api/v1/agent/runs/{run_id}/field-answer",
        json={"field_id": "sending_partner_name", "value": "Acme Payments"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["accepted"] is True
    assert body["next"]["kind"] in {"prompt", "recap"}

    event_types = await _run_event_types(agent_client, run_id)
    assert "v1.field.accepted" in event_types


async def test_field_answer_invalid_pattern_returns_422(agent_client: AsyncClient) -> None:
    conversation_id, run_id = await _start_wizard(agent_client)
    await _seed_draft(agent_client, conversation_id, {"summary": {}})

    # sending_partner_name enforces ^.{3,120}$ — a 2-char value fails.
    response = await agent_client.post(
        f"/api/v1/agent/runs/{run_id}/field-answer",
        json={"field_id": "sending_partner_name", "value": "ab"},
    )
    assert response.status_code == 422, response.text


async def test_field_answer_unknown_field_returns_422(agent_client: AsyncClient) -> None:
    conversation_id, run_id = await _start_wizard(agent_client)
    await _seed_draft(agent_client, conversation_id, {"summary": {}})

    response = await agent_client.post(
        f"/api/v1/agent/runs/{run_id}/field-answer",
        json={"field_id": "not_a_real_field", "value": "x"},
    )
    assert response.status_code == 422, response.text


async def test_field_answer_without_draft_returns_409(agent_client: AsyncClient) -> None:
    _conversation_id, run_id = await _start_wizard(agent_client)

    response = await agent_client.post(
        f"/api/v1/agent/runs/{run_id}/field-answer",
        json={"field_id": "sending_partner_name", "value": "Acme Payments"},
    )
    assert response.status_code == 409, response.text


async def test_conversation_commit_emits_committed_event(
    agent_client: AsyncClient, monkeypatch: Any
) -> None:
    conversation_id, run_id = await _start_wizard(agent_client)
    await _seed_draft(
        agent_client,
        conversation_id,
        {"summary": {"sending_partner_name": "Acme Payments", "opportunity_type": "new"}},
    )

    fake_result = {
        "quote_id": "q-1",
        "applied": ["create_quotation"],
        "failed": [],
    }

    async def fake_commit_draft(contract: Any, payload: Any, **_kw: Any) -> dict[str, Any]:
        return fake_result

    monkeypatch.setattr(
        "xframe_agent.api.v1.runs.commit_draft", fake_commit_draft
    )

    response = await agent_client.post(
        f"/api/v1/agent/runs/{run_id}/conversation-commit"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["committed"] is True
    assert body["quote_id"] == "q-1"
    assert body["applied"] == ["create_quotation"]

    event_types = await _run_event_types(agent_client, run_id)
    assert "v1.conversation.committed" in event_types


async def test_conversation_commit_without_draft_returns_409(agent_client: AsyncClient) -> None:
    _conversation_id, run_id = await _start_wizard(agent_client)

    response = await agent_client.post(
        f"/api/v1/agent/runs/{run_id}/conversation-commit"
    )
    assert response.status_code == 409, response.text
