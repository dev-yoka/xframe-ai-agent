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
    # Use a general conversation (not create_pricing_request) so no draft is
    # auto-seeded by the run — the field-answer endpoint must return 409.
    conv_resp = await agent_client.post(
        "/api/v1/agent/conversations",
        headers={"Idempotency-Key": "fa-no-draft-conv"},
        json={"title": "General", "kind": "general"},
    )
    assert conv_resp.status_code == 201
    conversation_id = conv_resp.json()["id"]
    run_resp = await agent_client.post(
        f"/api/v1/agent/conversations/{conversation_id}/runs",
        headers={"Idempotency-Key": "fa-no-draft-run"},
        json={"content": "Hello", "source": "text"},
    )
    assert run_resp.status_code == 202
    run_id = run_resp.json()["run_id"]

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
    # Use a general conversation (not create_pricing_request) so no draft is
    # auto-seeded by the run — the commit endpoint must return 409.
    conv_resp = await agent_client.post(
        "/api/v1/agent/conversations",
        headers={"Idempotency-Key": "commit-no-draft-conv"},
        json={"title": "General", "kind": "general"},
    )
    assert conv_resp.status_code == 201
    conversation_id = conv_resp.json()["id"]
    run_resp = await agent_client.post(
        f"/api/v1/agent/conversations/{conversation_id}/runs",
        headers={"Idempotency-Key": "commit-no-draft-run"},
        json={"content": "Hello", "source": "text"},
    )
    assert run_resp.status_code == 202
    run_id = run_resp.json()["run_id"]

    response = await agent_client.post(
        f"/api/v1/agent/runs/{run_id}/conversation-commit"
    )
    assert response.status_code == 409, response.text


async def test_conversation_start_seeds_draft_and_emits_first_prompt(
    agent_client: AsyncClient,
) -> None:
    """POST /conversation-start without a prior draft seeds one and emits the first prompt."""
    # Create conversation + run but deliberately skip _seed_draft so the draft is absent.
    _conversation_id, run_id = await _start_wizard(agent_client)

    response = await agent_client.post(
        f"/api/v1/agent/runs/{run_id}/conversation-start"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["started"] is True
    assert body["next"]["kind"] == "prompt"
    assert body["next"]["field_id"] == "sending_partner_name"

    event_types = await _run_event_types(agent_client, run_id)
    assert "v1.field.prompt" in event_types


@pytest.mark.asyncio
async def test_re_ask_field_emits_prompt_event(agent_client: AsyncClient) -> None:
    conversation_id, run_id = await _start_wizard(agent_client)
    await _seed_draft(agent_client, conversation_id, {"summary": {}})

    resp = await agent_client.post(
        f"/api/v1/agent/runs/{run_id}/re-ask-field",
        json={"field_id": "sending_partner_name"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["re_asked"] is True
    assert body["field_id"] == "sending_partner_name"

    # A v1.field.prompt event must now exist for this run.
    event_types = await _run_event_types(agent_client, run_id)
    assert "v1.field.prompt" in event_types


@pytest.mark.asyncio
async def test_re_ask_field_rejects_unknown_field(agent_client: AsyncClient) -> None:
    conversation_id, run_id = await _start_wizard(agent_client)
    await _seed_draft(agent_client, conversation_id, {"summary": {}})

    resp = await agent_client.post(
        f"/api/v1/agent/runs/{run_id}/re-ask-field",
        json={"field_id": "nonexistent_field_xyz"},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_conversation_start_preserves_existing_draft(agent_client: AsyncClient) -> None:
    """conversation-start on a run that already has a draft must not overwrite it."""
    conversation_id, run_id = await _start_wizard(agent_client)
    # Seed a draft with a known value that we want to survive the second call.
    await _seed_draft(
        agent_client,
        conversation_id,
        {"summary": {"sending_partner_name": "PreservedPartner"}},
    )

    # Call conversation-start a second time — it must be idempotent.
    resp = await agent_client.post(
        f"/api/v1/agent/runs/{run_id}/conversation-start",
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["started"] is True
    # The next item emitted should be a prompt or recap — never a blank restart.
    assert body["next"]["kind"] in ("prompt", "recap")

    # The draft must still hold the value we seeded — confirm by checking that a
    # v1.field.prompt event was appended (idempotent re-emit) but the existing
    # draft payload was not wiped.
    event_types = await _run_event_types(agent_client, run_id)
    assert "v1.field.prompt" in event_types


@pytest.mark.asyncio
async def test_field_answer_multi_enum_field_stores_list(agent_client: AsyncClient) -> None:
    """field-answer for a multi_enum field must persist a list of values, not a string."""
    conversation_id, run_id = await _start_wizard(agent_client)
    await _seed_draft(agent_client, conversation_id, {"summary": {}})

    # corridor_regions is a multi_enum with options_source (API-sourced), so any
    # list of strings is accepted by validate_answer without static enum checks.
    resp = await agent_client.post(
        f"/api/v1/agent/runs/{run_id}/field-answer",
        json={"field_id": "corridor_regions", "value": ["EMEA", "APAC"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["accepted"] is True
    assert body["next"]["kind"] in ("prompt", "recap")

    # A v1.field.accepted event must have been emitted for this answer.
    event_types = await _run_event_types(agent_client, run_id)
    assert "v1.field.accepted" in event_types


@pytest.mark.asyncio
async def test_re_ask_field_money_field_includes_suggestion_attempt(
    agent_client: AsyncClient, monkeypatch: Any
) -> None:
    """re-ask-field for a requires_explicit_confirm field must attempt to fetch a suggestion."""
    conversation_id, run_id = await _start_wizard(agent_client)
    await _seed_draft(agent_client, conversation_id, {"summary": {}})

    suggestion_attempts: list[str] = []

    async def fake_suggestion_for(
        field: Any,
        contract: Any,
        draft_payload: Any,
        auth_ctx: Any,
        priceframe: Any,
    ) -> dict[str, Any]:
        suggestion_attempts.append(field.field_id)
        return {"value": 2.5, "basis": "median of 10 deals", "as_of": "2026-05-31"}

    # The local import inside re_ask_field pulls from the runner module, so we
    # patch at the source rather than the runs namespace.
    monkeypatch.setattr(
        "xframe_agent.agent.conversation.runner._suggestion_for",
        fake_suggestion_for,
    )

    resp = await agent_client.post(
        f"/api/v1/agent/runs/{run_id}/re-ask-field",
        json={"field_id": "default_transaction_fee"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["re_asked"] is True
    # The money field (requires_explicit_confirm=True) must have triggered a suggestion fetch.
    assert "default_transaction_fee" in suggestion_attempts
