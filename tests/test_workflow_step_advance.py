"""Per-tab step advance + bundled ToolDecision tests (M2-WRITE-02)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine
from sse_starlette.sse import AppStatus

import xframe_agent.models  # noqa: F401
from xframe_agent.agent.workflow_advance import (
    resolve_args_template,
)
from xframe_agent.auth.dependencies import get_auth_context
from xframe_agent.auth.jwt import AuthContext
from xframe_agent.db.base import Base
from xframe_agent.main import create_app
from xframe_agent.models import AgentRunStep, AgentToolCall
from xframe_agent.models.agent import utc_now
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
    """Create a conversation + a run that the wizard can attach to."""

    create_response = await agent_client.post(
        "/api/v1/agent/conversations",
        headers={"Idempotency-Key": "wf-step-conversation"},
        json={"title": "Wizard", "kind": "create_pricing_request"},
    )
    assert create_response.status_code == 201
    conversation_id = create_response.json()["id"]
    run_response = await agent_client.post(
        f"/api/v1/agent/conversations/{conversation_id}/runs",
        headers={"Idempotency-Key": "wf-step-run"},
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


async def _seed_succeeded_create_quotation(
    app, run_id: str, quote_id: int
) -> None:
    """Insert a synthetic completed create_quotation row so the resolver finds
    ``created_quote_id`` without going through the model loop."""

    session_factory = app.state.session_factory
    async with session_factory() as session:
        step = AgentRunStep(
            run_id=run_id,
            seq=99,
            kind="tool_call",
            status="completed",
        )
        session.add(step)
        await session.flush()
        session.add(
            AgentToolCall(
                run_id=run_id,
                step_id=step.id,
                tool_name="create_quotation",
                status="succeeded",
                args={"name": "Seeded"},
                result={"data": {"id": quote_id, "name": "Seeded"}},
                completed_at=utc_now(),
            )
        )
        await session.commit()


def test_resolve_field_token() -> None:
    template = {
        "name": "{{field:summary.sending_partner_name}}",
        "currency": "{{field:summary.default_fee_currency}}",
        "literal": 42,
    }
    draft_payload = {
        "summary": {"sending_partner_name": "Acme", "default_fee_currency": "USD"},
    }
    resolved, unresolved = resolve_args_template(
        template,
        draft_payload=draft_payload,
        runtime_values={},
    )
    assert unresolved == []
    assert resolved == {"name": "Acme", "currency": "USD", "literal": 42}


def test_resolve_draft_runtime_value() -> None:
    template = {
        "quote_id": "{{draft.created_quote_id}}",
        "payload": "{{draft.pricing}}",
    }
    draft_payload = {"pricing": {"default_transaction_fee": "1.50"}}
    resolved, unresolved = resolve_args_template(
        template,
        draft_payload=draft_payload,
        runtime_values={"created_quote_id": 4242},
    )
    assert unresolved == []
    assert resolved == {
        "quote_id": 4242,
        "payload": {"default_transaction_fee": "1.50"},
    }


def test_resolve_records_unresolved_tokens() -> None:
    template = {"quote_id": "{{draft.created_quote_id}}"}
    resolved, unresolved = resolve_args_template(
        template,
        draft_payload={},
        runtime_values={},
    )
    assert resolved == {"quote_id": None}
    assert unresolved == ["{{draft.created_quote_id}}"]


async def test_batch_at_submit_step_advances_silently(agent_client: AsyncClient) -> None:
    conversation_id, run_id = await _start_wizard(agent_client)
    await _seed_draft(agent_client, conversation_id, {"summary": {}})

    response = await agent_client.post(
        f"/api/v1/agent/runs/{run_id}/step_advance",
        json={"step_id": "quoting_summary"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approved"
    assert body["tool_calls"] == []
    assert body["detail"]["reason"] == "batch_at_submit"


async def test_per_tab_without_on_complete_advances_silently(agent_client: AsyncClient) -> None:
    conversation_id, run_id = await _start_wizard(agent_client)
    await _seed_draft(agent_client, conversation_id, {"setup_fee": {}}, current_step_id="setup_fee")

    response = await agent_client.post(
        f"/api/v1/agent/runs/{run_id}/step_advance",
        json={"step_id": "setup_fee"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approved"
    assert body["tool_calls"] == []
    assert body["detail"]["reason"] == "no_on_complete"


async def test_per_tab_with_on_complete_emits_bundle(agent_client: AsyncClient) -> None:
    conversation_id, run_id = await _start_wizard(agent_client)
    await _seed_draft(
        agent_client,
        conversation_id,
        {"approvals": {"approval_comment": "ready to ship"}},
        current_step_id="approvals",
    )
    await _seed_succeeded_create_quotation(agent_client._transport.app, run_id, quote_id=4242)

    response = await agent_client.post(
        f"/api/v1/agent/runs/{run_id}/step_advance",
        json={"step_id": "approvals"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "requested"
    assert len(body["tool_calls"]) == 1
    proposal = body["tool_calls"][0]
    assert proposal["tool_name"] == "submit_for_approval"
    assert proposal["args"]["quote_id"] == 4242
    assert proposal["args"]["comment"] == "ready to ship"
    assert proposal["requires_approval"] is True

    # SSE stream should carry the advance_requested event for resume clients.
    stream_response = await agent_client.get(f"/api/v1/agent/runs/{run_id}/stream")
    assert stream_response.status_code == 200
    assert "event: v1.workflow.step.advance_requested" in stream_response.text


async def test_approvals_step_blocks_without_quote(agent_client: AsyncClient) -> None:
    conversation_id, run_id = await _start_wizard(agent_client)
    await _seed_draft(
        agent_client,
        conversation_id,
        {"approvals": {"approval_comment": "ready"}},
        current_step_id="approvals",
    )

    response = await agent_client.post(
        f"/api/v1/agent/runs/{run_id}/step_advance",
        json={"step_id": "approvals"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "blocked"
    assert body["reason"] == "missing_created_quote_id"


async def test_step_decision_rejection_marks_tool_calls(agent_client: AsyncClient) -> None:
    conversation_id, run_id = await _start_wizard(agent_client)
    await _seed_draft(
        agent_client,
        conversation_id,
        {"approvals": {"approval_comment": "needs more"}},
        current_step_id="approvals",
    )
    await _seed_succeeded_create_quotation(agent_client._transport.app, run_id, quote_id=99)
    advance = await agent_client.post(
        f"/api/v1/agent/runs/{run_id}/step_advance",
        json={"step_id": "approvals"},
    )
    assert advance.status_code == 200

    decision = await agent_client.post(
        f"/api/v1/agent/runs/{run_id}/step_decisions",
        json={"step_id": "approvals", "decision": "reject", "reason": "needs review"},
    )
    assert decision.status_code == 200
    body = decision.json()
    assert body["status"] == "rejected"
    assert body["reason"] == "needs review"

    # The stream contains both the request and the rejection events.
    stream_response = await agent_client.get(f"/api/v1/agent/runs/{run_id}/stream")
    assert stream_response.status_code == 200
    assert "event: v1.workflow.step.advance_requested" in stream_response.text
    assert "event: v1.workflow.step.advance_rejected" in stream_response.text


async def test_invalid_step_id_returns_422(agent_client: AsyncClient) -> None:
    conversation_id, run_id = await _start_wizard(agent_client)
    await _seed_draft(agent_client, conversation_id, {"summary": {}})

    response = await agent_client.post(
        f"/api/v1/agent/runs/{run_id}/step_advance",
        json={"step_id": "does_not_exist"},
    )
    assert response.status_code == 422
