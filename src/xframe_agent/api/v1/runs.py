"""Run status, decisions, cancellation, and SSE streaming endpoints."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from xframe_agent.agent.events import append_run_event, event_payload, list_run_events
from xframe_agent.auth.dependencies import get_auth_context
from xframe_agent.auth.jwt import AuthContext
from xframe_agent.db.session import get_session
from xframe_agent.models import AgentRun
from xframe_agent.models.agent import utc_now
from xframe_agent.schemas import DecisionRequest, RunResponse
from xframe_agent.settings import Settings, get_settings

router = APIRouter(tags=["runs"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AuthDep = Annotated[AuthContext, Depends(get_auth_context)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.get("/runs/{run_id}", response_model=RunResponse)
async def get_run(run_id: str, session: SessionDep, auth: AuthDep) -> RunResponse:
    run = await require_run(session, auth, run_id)
    return run_response(run)


@router.post("/runs/{run_id}/cancel", response_model=RunResponse)
async def cancel_run(run_id: str, session: SessionDep, auth: AuthDep) -> RunResponse:
    run = await require_run(session, auth, run_id)
    if run.status not in {"completed", "error", "cancelled"}:
        run.status = "cancelled"
        run.cancelled_at = utc_now()
        run.updated_at = utc_now()
        await append_run_event(
            session,
            run_id=run.id,
            event_type="v1.run.error",
            payload={"cancelled": True},
        )
        await session.commit()
    return run_response(run)


@router.post("/runs/{run_id}/decisions")
async def decide_run_tool_call(
    run_id: str,
    payload: DecisionRequest,
    session: SessionDep,
    auth: AuthDep,
) -> dict[str, object]:
    await require_run(session, auth, run_id)
    event_type_by_decision = {
        "approve": "v1.tool.approved",
        "reject": "v1.tool.rejected",
        "edit": "v1.tool.edited",
    }
    await append_run_event(
        session,
        run_id=run_id,
        event_type=event_type_by_decision[payload.decision],
        payload={"tool_call_id": payload.tool_call_id, "edited_args": payload.edited_args},
    )
    await session.commit()
    return {"success": True}


@router.get("/runs/{run_id}/stream")
async def stream_run(
    run_id: str,
    request: Request,
    session: SessionDep,
    auth: AuthDep,
    settings: SettingsDep,
    last_event_id_header: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> EventSourceResponse:
    await require_run(session, auth, run_id)
    query_cursor = request.query_params.get("last_event_id")
    last_event_id = _parse_cursor(query_cursor or last_event_id_header)

    async def generator() -> AsyncIterator[dict[str, str]]:
        emitted_terminal = False
        cursor = last_event_id
        while True:
            events = await list_run_events(
                session,
                run_id=run_id,
                after_seq=cursor,
                limit=settings.sse_replay_event_limit,
            )
            for event in events:
                cursor = event.seq
                if event.event_type in {"v1.run.completed", "v1.run.error"}:
                    emitted_terminal = True
                yield {
                    "id": str(event.seq),
                    "event": event.event_type,
                    "data": json.dumps(event_payload(event), separators=(",", ":")),
                }

            run = await session.get(AgentRun, run_id)
            if emitted_terminal or (run and run.status in {"completed", "error", "cancelled"}):
                break

            yield {
                "event": "v1.heartbeat",
                "data": json.dumps({"run_id": run_id, "seq": cursor}, separators=(",", ":")),
            }
            await asyncio.sleep(settings.sse_heartbeat_seconds)

    return EventSourceResponse(generator())


async def require_run(session: AsyncSession, auth: AuthContext, run_id: str) -> AgentRun:
    run = await session.get(AgentRun, run_id)
    if run is None or run.user_id != auth.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return run


def run_response(run: AgentRun) -> RunResponse:
    return RunResponse(
        id=run.id,
        conversation_id=run.conversation_id,
        status=run.status,
        input_message_id=run.input_message_id,
        output_message_id=run.output_message_id,
        error=run.error,
        created_at=run.created_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
        cancelled_at=run.cancelled_at,
    )


def _parse_cursor(value: str | None) -> int:
    if value and value.isdecimal():
        return int(value)
    return 0
