"""Verify that AgentLoop + ModelRunner emit v1.field.prompt (not v1.input.requested)
when a user sends "Create a pricing request" in a create_pricing_request conversation.

This test is intentionally written BEFORE the fix so it can serve as the regression
guard. It starts an actual in-process app (SQLite, inline execution mode) and checks
the SSE event stream produced by the run.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine
from sse_starlette.sse import AppStatus

import xframe_agent.models  # noqa: F401 — registers ORM metadata
from xframe_agent.auth.dependencies import get_auth_context
from xframe_agent.auth.jwt import AuthContext
from xframe_agent.db.base import Base
from xframe_agent.main import create_app
from xframe_agent.settings import Settings


@pytest.fixture
async def loop_client(
    test_settings: Settings, tmp_path: Path
) -> AsyncIterator[AsyncClient]:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'loop_conv.db'}"
    settings = test_settings.model_copy(
        update={
            "database_url": database_url,
            "run_execution_mode": "inline",
            "sse_redis_buffer_enabled": False,
        }
    )
    app = create_app(settings)

    async def fake_auth() -> AuthContext:
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

    app.dependency_overrides[get_auth_context] = fake_auth

    engine = create_async_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client

    AppStatus.should_exit = False
    AppStatus.should_exit_event = None
    await app.state.engine.dispose()


async def _start_wizard(client: AsyncClient) -> tuple[str, str]:
    """Create a create_pricing_request conversation and submit the trigger message."""
    conv_resp = await client.post(
        "/api/v1/agent/conversations",
        headers={"Idempotency-Key": "loop-conv-test"},
        json={"title": "Pricing wizard", "kind": "create_pricing_request"},
    )
    assert conv_resp.status_code == 201
    conversation_id = conv_resp.json()["id"]

    run_resp = await client.post(
        f"/api/v1/agent/conversations/{conversation_id}/runs",
        headers={"Idempotency-Key": "loop-conv-run-test"},
        json={"content": "Create a pricing request", "source": "text"},
    )
    assert run_resp.status_code == 202
    return conversation_id, run_resp.json()["run_id"]


async def test_loop_emits_field_prompt_not_input_requested(
    loop_client: AsyncClient,
) -> None:
    """Sending 'Create a pricing request' must emit v1.field.prompt.

    The old wizard path emitted v1.workflow.step.entered + v1.input.requested.
    The new conversational path must emit v1.field.prompt instead.
    """
    _conversation_id, run_id = await _start_wizard(loop_client)

    stream_resp = await loop_client.get(f"/api/v1/agent/runs/{run_id}/stream")
    assert stream_resp.status_code == 200
    stream_text = stream_resp.text

    # New conversational flow: first field prompt must appear.
    assert "event: v1.field.prompt" in stream_text, (
        "Expected v1.field.prompt in SSE stream but got:\n" + stream_text
    )

    # Old wizard events must NOT appear.
    assert "event: v1.input.requested" not in stream_text, (
        "Old v1.input.requested should no longer be emitted"
    )
    assert "event: v1.workflow.step.entered" not in stream_text, (
        "Old v1.workflow.step.entered should no longer be emitted"
    )

    # Run must still complete.
    assert "event: v1.run.completed" in stream_text
