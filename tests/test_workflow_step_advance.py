"""Per-tab step advance + bundled ToolDecision tests (M2-WRITE-02)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine
from sse_starlette.sse import AppStatus

import xframe_agent.models  # noqa: F401
from xframe_agent.agent.workflow_advance import (
    next_step_after,
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


def test_next_step_after_walks_contract() -> None:
    """Sanity check that ``next_step_after`` reads from the live contract."""

    from xframe_agent.agent.workflow_advance import load_contract

    contract = load_contract("create_pricing_request", "v1")
    after_summary = next_step_after(contract, "summary")
    assert after_summary is not None
    assert after_summary["id"] == "setup_fee"
    after_pnl = next_step_after(contract, "pnl")
    assert after_pnl is not None
    assert after_pnl["id"] == "quoting_summary"
    # The final step has no successor.
    assert next_step_after(contract, "approvals") is None


async def test_advance_approved_emits_step_entered_for_next_step(
    agent_client: AsyncClient,
) -> None:
    """Approving a per-tab step (no on_complete) emits step.entered for the next tab.

    ``setup_fee`` has ``approval_mode=per_tab`` with no ``on_complete`` block,
    so the request_step_advance endpoint returns ``approved`` immediately. The
    fix under test must emit ``v1.workflow.step.entered { step_id: "pricing" }``
    before closing the run with ``v1.run.completed`` — without that event the
    wizard never knows the user landed on the Pricing tab and the suggestion
    fan-out for that tab never runs.
    """

    conversation_id, run_id = await _start_wizard(agent_client)
    await _seed_draft(
        agent_client,
        conversation_id,
        {"setup_fee": {}, "summary": {"corridor": "USA-IND", "service": "C2C"}},
        current_step_id="setup_fee",
    )

    response = await agent_client.post(
        f"/api/v1/agent/runs/{run_id}/step_advance",
        json={"step_id": "setup_fee"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approved"

    stream_response = await agent_client.get(f"/api/v1/agent/runs/{run_id}/stream")
    assert stream_response.status_code == 200
    stream_text = stream_response.text
    # The new step.entered event must reference the NEXT step (pricing), not
    # the just-approved setup_fee.
    assert "event: v1.workflow.step.entered" in stream_text
    assert '"step_id":"pricing"' in stream_text
    # Ordering: the entered event for pricing must arrive AFTER the
    # advance_approved event for setup_fee and BEFORE the run.completed
    # event closes the stream.
    setup_fee_approved_index = stream_text.find(
        "event: v1.workflow.step.advance_approved"
    )
    pricing_entered_index = stream_text.find('"step_id":"pricing"')
    completed_index = stream_text.find('"via":"step_advance_approved"')
    assert setup_fee_approved_index > 0
    assert pricing_entered_index > setup_fee_approved_index
    assert completed_index > pricing_entered_index


async def test_advance_approved_fans_out_suggestions_for_next_step(
    test_settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the next step has proactive-historical fields and the caller has
    ``agent.suggestions.read``, per-field ``v1.suggestion.ready`` events follow
    the ``step.entered`` event in the SSE stream.

    We swap ``PriceFrameClient.from_settings`` for a recording fake so the test
    runs offline; the fake returns a scripted suggestion payload for each
    proactive-historical field on the ``pricing`` step.
    """

    from xframe_agent.priceframe import PriceFrameClient

    database_url = f"sqlite+aiosqlite:///{tmp_path / 'agent-fanout.db'}"
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
                "agent.suggestions.read",
            ),
            jwt_raw="jwt-for-tests",
            session_id=42,
        )

    app.dependency_overrides[get_auth_context] = fake_auth_context

    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()

    class _FakePriceFrame:
        def __init__(self) -> None:
            self.calls: list[Mapping[str, Any]] = []

        async def __aenter__(self) -> _FakePriceFrame:
            return self

        async def __aexit__(self, *_exc_info: object) -> None:
            return None

        async def get_json(
            self,
            path: str,  # noqa: ARG002
            *,
            jwt_raw: str,  # noqa: ARG002
            params: Mapping[str, Any] | None = None,
        ) -> dict[str, Any]:
            self.calls.append(dict(params or {}))
            field = (params or {}).get("field")
            return {
                "value": 1.5,
                "unit": "PCT_AMOUNT",
                "sample_size": 9,
                "range": {"min": 0.5, "max": 3.0, "p25": 1.0, "p75": 2.0},
                "basis": {"aggregation": "median", "filter_keys": ["corridor"]},
                "context_used": {"corridor": "USA-IND"},
                "as_of": "2026-05-25T00:00:00Z",
                "field_echo": field,
            }

    fake_pf = _FakePriceFrame()

    def fake_from_settings(
        _settings: Settings,
        *,
        default_headers: Mapping[str, str] | None = None,  # noqa: ARG001
    ) -> _FakePriceFrame:
        return fake_pf

    monkeypatch.setattr(
        PriceFrameClient, "from_settings", staticmethod(fake_from_settings)
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        try:
            conversation_id, run_id = await _start_wizard(client)
            await _seed_draft(
                client,
                conversation_id,
                {
                    "setup_fee": {},
                    "summary": {
                        "corridor": "USA-IND",
                        "service": "C2C",
                        "customer_segment": "RETAIL",
                    },
                },
                current_step_id="setup_fee",
            )
            response = await client.post(
                f"/api/v1/agent/runs/{run_id}/step_advance",
                json={"step_id": "setup_fee"},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["status"] == "approved"

            stream_response = await client.get(f"/api/v1/agent/runs/{run_id}/stream")
            assert stream_response.status_code == 200
            stream_text = stream_response.text
        finally:
            AppStatus.should_exit = False
            AppStatus.should_exit_event = None
            await app.state.engine.dispose()

    # step.entered for pricing must come BEFORE the per-field suggestion events.
    entered_index = stream_text.find('"step_id":"pricing"')
    assert entered_index >= 0
    ready_index = stream_text.find("event: v1.suggestion.ready")
    assert ready_index > entered_index
    # The pricing step declares 5 proactive-historical fields; each should
    # produce a v1.suggestion.ready event because the fake returns a value
    # above the (irrelevant in tests) min_sample_size.
    assert stream_text.count("event: v1.suggestion.ready") == 5
    # And the fake PriceFrameClient was called once per proactive-historical
    # field on the pricing step.
    assert len(fake_pf.calls) == 5


async def test_final_step_approval_does_not_emit_step_entered(
    test_settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Approving the last step (``approvals``) is the workflow terminus —
    there is no successor tab to enter, so no phantom ``step.entered`` event
    must appear in the SSE stream after ``step.advance_approved``.

    Uses a dedicated app + fake PriceFrameClient because the approval path
    executes ``submit_for_approval`` which calls PriceFRAME.
    """

    from xframe_agent.priceframe import PriceFrameClient

    database_url = f"sqlite+aiosqlite:///{tmp_path / 'agent-final.db'}"
    settings = test_settings.model_copy(
        update={
            "database_url": database_url,
            "run_execution_mode": "inline",
            "sse_redis_buffer_enabled": False,
            "priceframe_service_secret": "test-svc",
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

    class _FakePriceFrame:
        async def __aenter__(self) -> _FakePriceFrame:
            return self

        async def __aexit__(self, *_exc_info: object) -> None:
            return None

        async def post_json(
            self,
            path: str,  # noqa: ARG002
            *,
            jwt_raw: str,  # noqa: ARG002
            json: dict[str, Any] | None = None,  # noqa: ARG002
            headers: dict[str, str] | None = None,  # noqa: ARG002
        ) -> dict[str, Any]:
            return {"success": True, "data": {"id": 4242, "status": "submitted"}}

        async def post_agent_audit_callback(
            self,
            *,
            jwt_raw: str,  # noqa: ARG002
            service_secret: str,  # noqa: ARG002
            payload: dict[str, Any],  # noqa: ARG002
        ) -> int:
            return 1

    def fake_from_settings(
        _settings: Settings,
        *,
        default_headers: Mapping[str, str] | None = None,  # noqa: ARG001
    ) -> _FakePriceFrame:
        return _FakePriceFrame()

    monkeypatch.setattr(
        PriceFrameClient, "from_settings", staticmethod(fake_from_settings)
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        try:
            conversation_id, run_id = await _start_wizard(client)
            await _seed_draft(
                client,
                conversation_id,
                {"approvals": {"approval_comment": "ready to ship"}},
                current_step_id="approvals",
            )
            await _seed_succeeded_create_quotation(app, run_id, quote_id=4242)

            advance = await client.post(
                f"/api/v1/agent/runs/{run_id}/step_advance",
                json={"step_id": "approvals"},
            )
            assert advance.status_code == 200
            assert advance.json()["status"] == "requested"

            decision = await client.post(
                f"/api/v1/agent/runs/{run_id}/step_decisions",
                json={"step_id": "approvals", "decision": "approve"},
            )
            assert decision.status_code == 200
            assert decision.json()["status"] == "approved"

            stream_response = await client.get(f"/api/v1/agent/runs/{run_id}/stream")
            assert stream_response.status_code == 200
            stream_text = stream_response.text
        finally:
            AppStatus.should_exit = False
            AppStatus.should_exit_event = None
            await app.state.engine.dispose()

    assert "event: v1.workflow.step.advance_approved" in stream_text
    # The initial wizard mount emits one step.entered for ``summary`` — that
    # is expected. The fix under test must NOT emit a second step.entered
    # after approving the final step (``approvals``), because there is no
    # successor tab.
    advance_approved_index = stream_text.find(
        "event: v1.workflow.step.advance_approved"
    )
    assert advance_approved_index > 0
    tail_after_approval = stream_text[advance_approved_index:]
    assert "event: v1.workflow.step.entered" not in tail_after_approval
    # And only the initial summary step.entered should appear in the stream.
    assert stream_text.count("event: v1.workflow.step.entered") == 1
    assert '"step_id":"summary"' in stream_text
