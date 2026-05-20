"""Phase E write decision, attachment, and memory API tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

import xframe_agent.models  # noqa: F401
from xframe_agent.auth.dependencies import get_auth_context
from xframe_agent.auth.jwt import AuthContext
from xframe_agent.db.base import Base
from xframe_agent.main import create_app
from xframe_agent.models import (
    AgentAuditLog,
    AgentConversation,
    AgentRun,
    AgentRunEvent,
    AgentToolCall,
)
from xframe_agent.models.agent import AgentUserMemory
from xframe_agent.priceframe.client import PriceFrameClient
from xframe_agent.settings import Settings


class FakePriceFrameClient:
    """Minimal async PriceFRAME double for approved write decisions."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.audit_payloads: list[dict[str, Any]] = []

    async def __aenter__(self) -> FakePriceFrameClient:
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        return None

    async def put_json(
        self,
        path: str,
        *,
        jwt_raw: str,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        self.calls.append(("PUT", path, {"json": json, "headers": headers, "jwt_raw": jwt_raw}))
        return {"success": True, "data": {"id": 99, "appliedFxSpread": "0.0200"}}

    async def post_agent_audit_callback(
        self,
        *,
        jwt_raw: str,
        service_secret: str,
        payload: dict[str, Any],
    ) -> int:
        self.audit_payloads.append(
            {"jwt_raw": jwt_raw, "service_secret": service_secret, "payload": payload}
        )
        return 321


@pytest.fixture
async def phase_e_app_client(
    test_settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[AsyncClient, Any, FakePriceFrameClient]]:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'agent.db'}"
    settings = test_settings.model_copy(
        update={
            "database_url": database_url,
            "attachment_storage_backend": "local",
            "attachment_local_storage_path": str(tmp_path / "blobs"),
            "priceframe_service_secret": "phase-e-service-secret",
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
                "agent.quotes.edit",
                "agent.quotes.recalc",
                "agent.approvals.submit",
                "agent.salesforce.read",
            ),
            jwt_raw="jwt-for-tests",
            session_id=42,
        )

    fake_priceframe = FakePriceFrameClient()
    monkeypatch.setattr(
        PriceFrameClient,
        "from_settings",
        classmethod(lambda cls, settings, default_headers=None: fake_priceframe),
    )
    app.dependency_overrides[get_auth_context] = fake_auth_context

    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client, app, fake_priceframe
    await app.state.engine.dispose()


async def test_tool_discovery_includes_registered_write_tools(
    phase_e_app_client: tuple[AsyncClient, Any, FakePriceFrameClient],
) -> None:
    client, _app, _fake_priceframe = phase_e_app_client

    response = await client.get("/api/v1/agent/tools")

    assert response.status_code == 200
    tool_names = {tool["name"] for tool in response.json()["tools"]}
    assert {
        "create_quotation",
        "bulk_add_corridors",
        "update_corridor_pricing",
        "set_fx_spread",
        "submit_for_approval",
        "preview_pricing_change",
    }.issubset(tool_names)


async def test_approved_write_decision_executes_tool_and_records_audit(
    phase_e_app_client: tuple[AsyncClient, Any, FakePriceFrameClient],
) -> None:
    client, app, fake_priceframe = phase_e_app_client
    async with app.state.session_factory() as session:
        conversation = AgentConversation(user_id=7, title="Write decision")
        session.add(conversation)
        await session.flush()
        run = AgentRun(conversation_id=conversation.id, user_id=7, status="awaiting_decision")
        session.add(run)
        await session.flush()
        tool_call = AgentToolCall(
            run_id=run.id,
            tool_name="update_corridor_pricing",
            status="proposed",
            requires_approval=True,
            args={"corridor_id": 99, "fx_spread": "0.0200"},
        )
        session.add(tool_call)
        await session.commit()
        run_id = run.id
        tool_call_id = tool_call.id

    response = await client.post(
        f"/api/v1/agent/runs/{run_id}/decisions",
        json={"tool_call_id": tool_call_id, "decision": "approve"},
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert fake_priceframe.calls == [
        (
            "PUT",
            "/api/quote-corridors/99",
            {
                "json": {"fxSpread": "0.0200"},
                "headers": None,
                "jwt_raw": "jwt-for-tests",
            },
        )
    ]
    assert fake_priceframe.audit_payloads[0]["payload"]["entity"] == "quote_corridor"
    assert fake_priceframe.audit_payloads[0]["payload"]["entity_id"] == 99
    assert fake_priceframe.audit_payloads[0]["payload"]["agent_tool_call_id"] == tool_call_id

    async with app.state.session_factory() as session:
        stored = await session.get(AgentToolCall, tool_call_id)
        assert stored is not None
        assert stored.status == "succeeded"
        assert stored.priceframe_audit_log_id == 321
        audit_rows = (await session.execute(select(AgentAuditLog))).scalars().all()
        assert len(audit_rows) == 1
        assert audit_rows[0].action == "update_corridor_pricing"


async def test_run_can_propose_write_tool_for_human_decision(
    phase_e_app_client: tuple[AsyncClient, Any, FakePriceFrameClient],
) -> None:
    client, app, _fake_priceframe = phase_e_app_client

    create_response = await client.post(
        "/api/v1/agent/conversations",
        headers={"Idempotency-Key": "phase-e-conversation"},
        json={"title": "Tool proposal"},
    )
    assert create_response.status_code == 201
    conversation_id = create_response.json()["id"]

    run_response = await client.post(
        f"/api/v1/agent/conversations/{conversation_id}/runs",
        headers={"Idempotency-Key": "phase-e-run"},
        json={
            "content": (
                'tool: {"name": "set_fx_spread", "args": '
                '{"corridor_id": 99, "applied_fx_spread": "0.0200", "minimum_spread": "0.0100"}}'
            )
        },
    )

    assert run_response.status_code == 202
    run_id = run_response.json()["run_id"]
    snapshot = await client.get(f"/api/v1/agent/runs/{run_id}")
    assert snapshot.status_code == 200
    assert snapshot.json()["status"] == "awaiting_decision"

    async with app.state.session_factory() as session:
        tool_calls = (
            (await session.execute(select(AgentToolCall).where(AgentToolCall.run_id == run_id)))
            .scalars()
            .all()
        )
        assert len(tool_calls) == 1
        assert tool_calls[0].tool_name == "set_fx_spread"
        assert tool_calls[0].requires_approval is True
        events = (
            (await session.execute(select(AgentRunEvent).where(AgentRunEvent.run_id == run_id)))
            .scalars()
            .all()
        )
        assert "v1.tool.proposed" in {event.event_type for event in events}


async def test_attachment_upload_and_read_metadata(
    phase_e_app_client: tuple[AsyncClient, Any, FakePriceFrameClient],
) -> None:
    client, _app, _fake_priceframe = phase_e_app_client

    upload = await client.post(
        "/api/v1/agent/attachments",
        files={"file": ("quote-note.txt", b"hello pricing", "text/plain")},
    )

    assert upload.status_code == 201
    uploaded = upload.json()
    assert uploaded["filename"] == "quote-note.txt"
    assert uploaded["status"] == "ready"
    assert uploaded["scan_status"] == "clean"

    read = await client.get(f"/api/v1/agent/attachments/{uploaded['id']}")
    assert read.status_code == 200
    assert read.json()["checksum_sha256"] == uploaded["checksum_sha256"]


async def test_memory_list_and_delete(
    phase_e_app_client: tuple[AsyncClient, Any, FakePriceFrameClient],
) -> None:
    client, app, _fake_priceframe = phase_e_app_client
    async with app.state.session_factory() as session:
        memory = AgentUserMemory(user_id=7, key="preference", value="prefers concise summaries")
        session.add(memory)
        await session.commit()
        memory_id = memory.id

    listed = await client.get("/api/v1/agent/memory")
    assert listed.status_code == 200
    assert listed.json()["memories"][0]["id"] == memory_id

    deleted = await client.delete(f"/api/v1/agent/memory/{memory_id}")
    assert deleted.status_code == 204

    listed_again = await client.get("/api/v1/agent/memory")
    assert listed_again.status_code == 200
    assert listed_again.json()["memories"] == []


async def test_voice_transcription_requires_groq_configuration(
    phase_e_app_client: tuple[AsyncClient, Any, FakePriceFrameClient],
) -> None:
    client, _app, _fake_priceframe = phase_e_app_client

    response = await client.post(
        "/api/v1/agent/voice/transcriptions",
        files={"file": ("prompt.webm", b"audio", "audio/webm")},
    )

    assert response.status_code == 503
