"""Run status, decisions, cancellation, and SSE streaming endpoints."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from xframe_agent.agent.events import append_run_event, event_payload, list_run_events
from xframe_agent.auth.dependencies import get_auth_context
from xframe_agent.auth.jwt import AuthContext
from xframe_agent.db.session import get_session
from xframe_agent.models import AgentAuditLog, AgentRun, AgentToolCall
from xframe_agent.models.agent import utc_now
from xframe_agent.priceframe import PriceFrameClient
from xframe_agent.priceframe.errors import PriceFrameError
from xframe_agent.schemas import DecisionRequest, PendingToolCallResponse, RunResponse
from xframe_agent.settings import Settings, get_settings
from xframe_agent.tools.base import ToolPermissionError
from xframe_agent.tools.registry import tool_registry

router = APIRouter(tags=["runs"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AuthDep = Annotated[AuthContext, Depends(get_auth_context)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.get("/runs/{run_id}", response_model=RunResponse)
async def get_run(run_id: str, session: SessionDep, auth: AuthDep) -> RunResponse:
    run = await require_run(session, auth, run_id)
    pending_tool_calls = await list_pending_tool_calls(session, run_id)
    return run_response(run, pending_tool_calls=pending_tool_calls)


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
    settings: SettingsDep,
    request: Request,
) -> dict[str, object]:
    run = await require_run(session, auth, run_id)
    tool_call = await require_tool_call(session, run_id, payload.tool_call_id)
    event_type_by_decision = {
        "approve": "v1.tool.approved",
        "reject": "v1.tool.rejected",
        "edit": "v1.tool.edited",
    }

    if payload.decision == "reject":
        tool_call.status = "rejected"
        tool_call.rejected_at = utc_now()
        tool_call.completed_at = utc_now()
        run.updated_at = utc_now()
        await append_run_event(
            session,
            run_id=run_id,
            event_type=event_type_by_decision[payload.decision],
            payload={"tool_call_id": payload.tool_call_id, "edited_args": None},
        )
        await session.commit()
        return {"success": True, "tool_call_status": tool_call.status}

    if payload.decision == "edit":
        if payload.edited_args is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="edited_args is required for edit decisions",
            )
        tool_call.args = dict(payload.edited_args)
        tool_call.status = "proposed"
        run.updated_at = utc_now()
        await append_run_event(
            session,
            run_id=run_id,
            event_type=event_type_by_decision[payload.decision],
            payload={"tool_call_id": payload.tool_call_id, "edited_args": payload.edited_args},
        )
        await session.commit()
        return {"success": True, "tool_call_status": tool_call.status}

    if payload.decision == "approve" and tool_call.status == "executing":
        return {"success": True, "tool_call_status": tool_call.status}
    if tool_call.status == "succeeded":
        return {"success": True, "tool_call_status": tool_call.status, "result": tool_call.result}
    if tool_call.status not in {"pending", "proposed", "awaiting_approval"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Tool call cannot be approved from status {tool_call.status}",
        )

    tool = tool_registry.get(tool_call.tool_name)
    if tool is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown tool")

    call_args: dict[str, Any] = dict(payload.edited_args or tool_call.args)
    parsed_args = tool.input_model.model_validate(call_args)
    tool_call.args = parsed_args.model_dump(mode="json")
    tool_call.status = "executing"
    tool_call.approved_at = utc_now()
    run.updated_at = utc_now()
    await append_run_event(
        session,
        run_id=run_id,
        event_type=event_type_by_decision[payload.decision],
        payload={"tool_call_id": payload.tool_call_id, "edited_args": payload.edited_args},
    )
    await append_run_event(
        session,
        run_id=run_id,
        event_type="v1.tool.started",
        payload={"tool_call_id": payload.tool_call_id, "tool_name": tool_call.tool_name},
    )
    await session.commit()

    try:
        async with PriceFrameClient.from_settings(
            settings,
            default_headers={"Idempotency-Key": tool_call.id},
        ) as priceframe:
            result_model = await tool.execute(parsed_args, auth, priceframe)
            result_payload = result_model.model_dump(mode="json")
            audit_log_id: int | None = None
            if tool.risk != "READ":
                audit_payload = _audit_payload(
                    run_id=run_id,
                    tool_call=tool_call,
                    args=call_args,
                    request=request,
                )
                audit_log_id = await priceframe.post_agent_audit_callback(
                    jwt_raw=auth.jwt_raw,
                    service_secret=settings.priceframe_service_secret,
                    payload=audit_payload,
                )
    except ToolPermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except PriceFrameError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    tool_call.status = "succeeded"
    tool_call.result = result_payload
    tool_call.priceframe_audit_log_id = audit_log_id
    tool_call.completed_at = utc_now()
    run.updated_at = utc_now()
    if run.status == "awaiting_decision":
        run.status = "completed"
        run.completed_at = utc_now()
    if tool.risk != "READ":
        session.add(
            AgentAuditLog(
                user_id=auth.user_id,
                run_id=run_id,
                action=tool_call.tool_name,
                payload={
                    "tool_call_id": tool_call.id,
                    "priceframe_audit_log_id": audit_log_id,
                    "args": call_args,
                    "result": result_payload,
                },
            )
        )
    await append_run_event(
        session,
        run_id=run_id,
        event_type="v1.tool.completed",
        payload={"tool_call_id": payload.tool_call_id, "result": result_payload},
    )
    await session.commit()
    return {"success": True, "tool_call_status": tool_call.status, "result": result_payload}


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
                if event.event_type in {
                    "v1.run.awaiting_decision",
                    "v1.run.completed",
                    "v1.run.error",
                }:
                    emitted_terminal = True
                yield {
                    "id": str(event.seq),
                    "event": event.event_type,
                    "data": json.dumps(event_payload(event), separators=(",", ":")),
                }

            run = await session.get(AgentRun, run_id)
            if emitted_terminal or (
                run and run.status in {"awaiting_decision", "completed", "error", "cancelled"}
            ):
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


async def require_tool_call(
    session: AsyncSession,
    run_id: str,
    tool_call_id: str,
) -> AgentToolCall:
    result = await session.execute(
        select(AgentToolCall).where(
            AgentToolCall.id == tool_call_id,
            AgentToolCall.run_id == run_id,
        )
    )
    tool_call = result.scalar_one_or_none()
    if tool_call is None:
        pending_tool_calls = await list_pending_tool_calls(session, run_id)
        if pending_tool_calls:
            available = ", ".join(call.id for call in pending_tool_calls)
            detail = f"Tool call not found for this run. Pending tool_call_id values: {available}"
        else:
            detail = "Tool call not found for this run. No pending tool calls exist for this run."
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
    return tool_call


async def list_pending_tool_calls(session: AsyncSession, run_id: str) -> list[AgentToolCall]:
    result = await session.execute(
        select(AgentToolCall)
        .where(
            AgentToolCall.run_id == run_id,
            AgentToolCall.status.in_(("pending", "proposed", "awaiting_approval")),
        )
        .order_by(AgentToolCall.created_at.asc())
    )
    return list(result.scalars().all())


def _audit_payload(
    *,
    run_id: str,
    tool_call: AgentToolCall,
    args: dict[str, Any],
    request: Request,
) -> dict[str, Any]:
    entity, entity_id = _audit_entity(tool_call.tool_name, args)
    return {
        "agent_run_id": run_id,
        "agent_tool_call_id": tool_call.id,
        "entity": entity,
        "entity_id": entity_id,
        "action": tool_call.tool_name,
        "changes": args,
        "user_ip": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
    }


def _audit_entity(tool_name: str, args: dict[str, Any]) -> tuple[str, int]:
    if tool_name in {"update_corridor_pricing", "set_fx_spread"}:
        return "quote_corridor", int(args["corridor_id"])
    if tool_name in {"bulk_add_corridors", "submit_for_approval", "preview_pricing_change"}:
        return "quote", int(args["quote_id"])
    if tool_name == "create_quotation":
        candidate = args.get("quote_id") or args.get("id") or 0
        return "quote", int(candidate)
    return "agent_tool_call", 0


def pending_tool_call_response(tool_call: AgentToolCall) -> PendingToolCallResponse:
    return PendingToolCallResponse(
        id=tool_call.id,
        tool_name=tool_call.tool_name,
        status=tool_call.status,
        args=dict(tool_call.args),
        requires_approval=tool_call.requires_approval,
    )


def run_response(
    run: AgentRun,
    *,
    pending_tool_calls: list[AgentToolCall] | None = None,
) -> RunResponse:
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
        pending_tool_calls=[
            pending_tool_call_response(tool_call) for tool_call in pending_tool_calls or []
        ],
    )


def _parse_cursor(value: str | None) -> int:
    if value and value.isdecimal():
        return int(value)
    return 0
