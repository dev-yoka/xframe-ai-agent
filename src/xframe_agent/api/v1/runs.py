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

from xframe_agent.agent.events import (
    append_run_event,
    event_payload,
    list_run_events,
    record_step_duration_from_events,
)
from xframe_agent.agent.suggestions import emit_suggestions
from xframe_agent.agent.workflow_advance import (
    StepAdvanceError,
    emit_advance_event,
    emit_step_entered_with_suggestions,
    evaluate_step_advance,
    load_contract,
    next_step_after,
)
from xframe_agent.auth.dependencies import get_auth_context
from xframe_agent.auth.jwt import AuthContext
from xframe_agent.db.session import get_session
from xframe_agent.models import (
    AgentAuditLog,
    AgentConversation,
    AgentRun,
    AgentRunStep,
    AgentToolCall,
    AgentWorkflowDraft,
)
from xframe_agent.models.agent import utc_now
from xframe_agent.priceframe import PriceFrameClient
from xframe_agent.priceframe.errors import (
    PriceFrameAuthError,
    PriceFrameError,
    PriceFrameForbiddenError,
    PriceFrameNotFoundError,
)
from xframe_agent.schemas import (
    DecisionRequest,
    PendingToolCallResponse,
    RunResponse,
    StepAdvanceRequest,
    StepAdvanceResponse,
    StepAdvanceToolCall,
    StepDecisionRequest,
    StepDecisionResponse,
)
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
    tool_call.args = tool.input_model.model_validate(call_args).model_dump(mode="json")
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
        result_payload = await _execute_approved_tool_call(
            session,
            tool_call=tool_call,
            run=run,
            auth=auth,
            settings=settings,
            request=request,
            mark_run_completed=True,
        )
    except ToolPermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except PriceFrameError as exc:
        raise HTTPException(
            status_code=_priceframe_error_status(exc),
            detail=str(exc),
        ) from exc
    await session.commit()
    return {"success": True, "tool_call_status": tool_call.status, "result": result_payload}


async def _execute_approved_tool_call(
    session: AsyncSession,
    *,
    tool_call: AgentToolCall,
    run: AgentRun,
    auth: AuthContext,
    settings: Settings,
    request: Request,
    mark_run_completed: bool,
) -> dict[str, Any]:
    """Execute a tool call that the user already approved.

    Mirrors the legacy path inside ``decide_run_tool_call`` so the new bundled
    step-decision endpoint can reuse it for each call in the bundle. Audit
    callbacks and ``AgentAuditLog`` rows are written exactly the same way.
    """

    tool = tool_registry.get(tool_call.tool_name)
    if tool is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown tool")
    call_args: dict[str, Any] = dict(tool_call.args)
    parsed_args = tool.input_model.model_validate(call_args)
    async with PriceFrameClient.from_settings(
        settings,
        default_headers={"Idempotency-Key": tool_call.id},
    ) as priceframe:
        result_model = await tool.execute(parsed_args, auth, priceframe)
        result_payload: dict[str, Any] = result_model.model_dump(mode="json")
        audit_log_id: int | None = None
        if tool.risk != "READ":
            audit_payload = _audit_payload(
                run_id=run.id,
                tool_call=tool_call,
                args=call_args,
                result=result_payload,
                request=request,
            )
            audit_log_id = await priceframe.post_agent_audit_callback(
                jwt_raw=auth.jwt_raw,
                service_secret=settings.priceframe_service_secret,
                payload=audit_payload,
            )

    tool_call.status = "succeeded"
    tool_call.result = result_payload
    tool_call.priceframe_audit_log_id = audit_log_id
    tool_call.completed_at = utc_now()
    run.updated_at = utc_now()
    if mark_run_completed and run.status == "awaiting_decision":
        run.status = "completed"
        run.completed_at = utc_now()
    if tool.risk != "READ":
        session.add(
            AgentAuditLog(
                user_id=auth.user_id,
                run_id=run.id,
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
        run_id=run.id,
        event_type="v1.tool.completed",
        payload={"tool_call_id": tool_call.id, "result": result_payload},
    )
    return result_payload


@router.post("/runs/{run_id}/step_advance", response_model=StepAdvanceResponse)
async def request_step_advance(
    run_id: str,
    payload: StepAdvanceRequest,
    session: SessionDep,
    auth: AuthDep,
    settings: SettingsDep,
) -> StepAdvanceResponse:
    """Evaluate the bundled ToolDecision for a per-tab step click of Next."""

    run = await require_run(session, auth, run_id)
    # The wizard reuses the same run across multiple per-tab advances. A run
    # may already be ``completed`` from an earlier model turn; reopen it so the
    # SSE stream (and decision events) belong to a consistent session.
    if run.status in {"completed", "error"}:
        run.status = "awaiting_decision"
        run.completed_at = None
        run.updated_at = utc_now()

    draft = await session.get(AgentWorkflowDraft, run.conversation_id)
    try:
        decision = await evaluate_step_advance(
            session,
            run=run,
            context=auth,
            step_id=payload.step_id,
            draft=draft,
        )
    except StepAdvanceError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc

    await emit_advance_event(session, run=run, step_id=payload.step_id, decision=decision)
    # Mark the run completed when the step required no proposal so the SSE
    # stream closes cleanly; keep it ``awaiting_decision`` while a bundle is
    # pending so the client can pick up the upcoming events.
    if decision.status == "approved":
        # M2-OBSERVE: record step duration (entered -> approved) before we
        # enter the next tab so the metric reflects the user-perceived time.
        await record_step_duration_from_events(
            session,
            run_id=run.id,
            step_id=payload.step_id,
            outcome="approved",
        )
        # Emit step.entered + historical suggestions for the next tab BEFORE
        # the run.completed event, so SSE consumers see "you're now on tab N+1"
        # plus any pre-filled values before the stream closes.
        await _enter_next_step_after_advance(
            session,
            run_id=run.id,
            conversation_id=run.conversation_id,
            approved_step_id=payload.step_id,
            auth=auth,
            settings=settings,
        )
        run.status = "completed"
        run.completed_at = utc_now()
        run.updated_at = utc_now()
        await append_run_event(
            session,
            run_id=run.id,
            event_type="v1.run.completed",
            payload={"step_id": payload.step_id, "via": "step_advance_approved"},
        )
    elif decision.status == "blocked":
        await record_step_duration_from_events(
            session,
            run_id=run.id,
            step_id=payload.step_id,
            outcome="blocked",
        )
        run.status = "completed"
        run.completed_at = utc_now()
        run.updated_at = utc_now()
        await append_run_event(
            session,
            run_id=run.id,
            event_type="v1.run.completed",
            payload={"step_id": payload.step_id, "via": "step_advance_blocked"},
        )
    await session.commit()

    response_tool_calls: list[StepAdvanceToolCall] = []
    for index, call in enumerate(decision.tool_calls):
        tool_call_id = (
            decision.persisted_tool_call_ids[index]
            if index < len(decision.persisted_tool_call_ids)
            else ""
        )
        tool = tool_registry.get(call.tool_name)
        requires_approval = tool.risk != "READ" if tool is not None else True
        response_tool_calls.append(
            StepAdvanceToolCall(
                tool_call_id=tool_call_id,
                tool_name=call.tool_name,
                args=call.args,
                requires_approval=requires_approval,
            )
        )
    assert decision.status in ("approved", "requested", "blocked")
    return StepAdvanceResponse.model_validate(
        {
            "step_id": payload.step_id,
            "status": decision.status,
            "tool_calls": [tc.model_dump() for tc in response_tool_calls],
            "reason": decision.blocked_reason,
            "detail": decision.detail,
        }
    )


@router.post("/runs/{run_id}/step_decisions", response_model=StepDecisionResponse)
async def decide_step_advance(
    run_id: str,
    payload: StepDecisionRequest,
    session: SessionDep,
    auth: AuthDep,
    settings: SettingsDep,
    request: Request,
) -> StepDecisionResponse:
    """Approve or reject a previously-emitted bundled step proposal."""

    run = await require_run(session, auth, run_id)
    bundled_tool_calls = await _list_proposed_tool_calls_for_step(
        session,
        run_id=run_id,
        step_id=payload.step_id,
    )
    if not bundled_tool_calls:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No pending bundled proposal for this step",
        )

    if payload.decision == "reject":
        for tool_call in bundled_tool_calls:
            tool_call.status = "rejected"
            tool_call.rejected_at = utc_now()
            tool_call.completed_at = utc_now()
            await append_run_event(
                session,
                run_id=run_id,
                event_type="v1.tool.rejected",
                payload={"tool_call_id": tool_call.id, "edited_args": None},
            )
        run.updated_at = utc_now()
        if run.status == "awaiting_decision":
            run.status = "completed"
            run.completed_at = utc_now()
        await record_step_duration_from_events(
            session,
            run_id=run_id,
            step_id=payload.step_id,
            outcome="rejected",
        )
        await append_run_event(
            session,
            run_id=run_id,
            event_type="v1.workflow.step.advance_rejected",
            payload={
                "step_id": payload.step_id,
                "status": "rejected",
                "reason": payload.reason,
                "tool_call_ids": [tc.id for tc in bundled_tool_calls],
            },
        )
        await append_run_event(
            session,
            run_id=run_id,
            event_type="v1.run.completed",
            payload={"step_id": payload.step_id, "via": "step_decision_rejected"},
        )
        await session.commit()
        return StepDecisionResponse(
            step_id=payload.step_id,
            status="rejected",
            reason=payload.reason,
        )

    # Approval path — execute each call serially (writes never run in parallel).
    tool_call_results: list[dict[str, Any]] = []
    for tool_call in bundled_tool_calls:
        tool_call.status = "executing"
        tool_call.approved_at = utc_now()
        await append_run_event(
            session,
            run_id=run_id,
            event_type="v1.tool.approved",
            payload={"tool_call_id": tool_call.id, "edited_args": None},
        )
        await append_run_event(
            session,
            run_id=run_id,
            event_type="v1.tool.started",
            payload={"tool_call_id": tool_call.id, "tool_name": tool_call.tool_name},
        )
        await session.commit()
        try:
            result_payload = await _execute_approved_tool_call(
                session,
                tool_call=tool_call,
                run=run,
                auth=auth,
                settings=settings,
                request=request,
                mark_run_completed=False,
            )
        except ToolPermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except PriceFrameError as exc:
            raise HTTPException(
                status_code=_priceframe_error_status(exc),
                detail=str(exc),
            ) from exc
        tool_call_results.append(
            {
                "tool_call_id": tool_call.id,
                "tool_name": tool_call.tool_name,
                "result": result_payload,
            }
        )

    run.updated_at = utc_now()
    if run.status == "awaiting_decision":
        run.status = "completed"
        run.completed_at = utc_now()
    await record_step_duration_from_events(
        session,
        run_id=run_id,
        step_id=payload.step_id,
        outcome="approved",
    )
    await append_run_event(
        session,
        run_id=run_id,
        event_type="v1.workflow.step.advance_approved",
        payload={
            "step_id": payload.step_id,
            "status": "approved",
            "tool_call_ids": [tc.id for tc in bundled_tool_calls],
        },
    )
    # Emit step.entered + historical suggestions for the next tab BEFORE the
    # run.completed event, so SSE consumers see ordered events: advance_approved
    # -> step.entered -> suggestion.ready/no_signal -> run.completed.
    await _enter_next_step_after_advance(
        session,
        run_id=run.id,
        conversation_id=run.conversation_id,
        approved_step_id=payload.step_id,
        auth=auth,
        settings=settings,
    )
    await append_run_event(
        session,
        run_id=run_id,
        event_type="v1.run.completed",
        payload={"step_id": payload.step_id, "via": "step_decision_approved"},
    )
    await session.commit()
    return StepDecisionResponse(
        step_id=payload.step_id,
        status="approved",
        tool_call_results=tool_call_results,
    )


async def _enter_next_step_after_advance(
    session: AsyncSession,
    *,
    run_id: str,
    conversation_id: str,
    approved_step_id: str,
    auth: AuthContext,
    settings: Settings,
) -> None:
    """Emit ``step.entered`` + fan out historical suggestions for the next step.

    Per-tab advance handlers call this after the just-approved step is
    persisted, so the wizard sees a fresh ``v1.workflow.step.entered`` plus
    per-field suggestion events for the tab the user is about to land on. The
    final step (``approvals``) has no successor and is skipped.

    The PriceFrame client is created lazily and only when the auth context has
    the ``agent.suggestions.read`` permission, because the only consumer of the
    client is the historical-suggestions fan-out — which itself gates on that
    permission. Skipping the construction keeps tests without PriceFRAME stubs
    from needing a fake settings.
    """

    draft = await session.get(AgentWorkflowDraft, conversation_id)
    contract_id = draft.contract_id if draft is not None else "create_pricing_request"
    contract_version = draft.contract_version if draft is not None else "v1"
    try:
        contract = load_contract(contract_id, contract_version)
    except StepAdvanceError:
        return
    next_step = next_step_after(contract, approved_step_id)
    if next_step is None:
        return
    draft_state = draft.payload if draft is not None else {}

    if auth.has_permission("agent.suggestions.read"):
        async with PriceFrameClient.from_settings(settings) as priceframe:
            await emit_step_entered_with_suggestions(
                session,
                run_id=run_id,
                contract=contract,
                step=next_step,
                draft_state=draft_state,
                auth_ctx=auth,
                priceframe=priceframe,
            )
    else:
        # No permission for suggestions — still emit the entered event so the
        # wizard knows which tab the user landed on, but skip the fan-out.
        await emit_step_entered_with_suggestions(
            session,
            run_id=run_id,
            contract=contract,
            step=next_step,
            draft_state=draft_state,
            auth_ctx=auth,
            priceframe=None,
        )


async def _resolve_run_or_conversation(
    session: AsyncSession,
    auth: AuthContext,
    ident: str,
) -> AgentRun:
    """Resolve a path parameter that may carry either a run_id or a conversation_id.

    The Wave C client (PriceFRAME commit ``77de2637``) passes the *conversation
    id* into the reactive suggestion route because the wizard hook tracks
    conversations rather than the per-run id. To keep the client trivial, this
    helper accepts both:

    1. If ``ident`` matches an ``AgentRun`` owned by the caller, use it.
    2. Otherwise look up an ``AgentConversation`` with the same id and return
       its most-recently-updated run.

    Returns 404 otherwise (also covers the cross-user case).
    """

    run = await session.get(AgentRun, ident)
    if run is not None and run.user_id == auth.user_id:
        return run
    conversation = await session.get(AgentConversation, ident)
    if conversation is None or conversation.user_id != auth.user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No run or conversation found for this id",
        )
    result = await session.execute(
        select(AgentRun)
        .where(AgentRun.conversation_id == conversation.id, AgentRun.user_id == auth.user_id)
        .order_by(AgentRun.updated_at.desc())
        .limit(1)
    )
    latest_run = result.scalar_one_or_none()
    if latest_run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active run for this conversation",
        )
    return latest_run


@router.post(
    "/runs/{run_or_conversation_id}/suggestions/{field_id}",
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_reactive_suggestion(
    run_or_conversation_id: str,
    field_id: str,
    session: SessionDep,
    auth: AuthDep,
    settings: SettingsDep,
) -> dict[str, str]:
    """On-demand suggestion for a single reactive-mode field (M2 / Phase 11).

    The wizard POSTs here when a user clicks "Ask the agent" on a reactive
    field. ``run_or_conversation_id`` may be either a real ``run_id`` or the
    parent ``conversation_id`` — the Wave C client passes the conversation
    id, so the server resolves both shapes transparently rather than forcing
    a client-side plumbing change.

    The fan-out is gated on ``agent.suggestions.read``. Successful runs emit
    a ``v1.suggestion.ready`` / ``v1.suggestion.no_signal`` event over SSE so
    the client picks up the response on the existing stream.
    """

    if not auth.has_permission("agent.suggestions.read"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing agent.suggestions.read permission",
        )

    run = await _resolve_run_or_conversation(session, auth, run_or_conversation_id)
    draft = await session.get(AgentWorkflowDraft, run.conversation_id)
    if draft is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active workflow draft for this conversation",
        )
    try:
        contract = load_contract(draft.contract_id, draft.contract_version)
    except StepAdvanceError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc

    step_id = draft.current_step_id
    step: Any | None = None
    steps_iter: list[Any] = contract.get("steps", []) if isinstance(contract, dict) else []
    for candidate in steps_iter:
        cid: Any = (
            candidate.get("id") if isinstance(candidate, dict) else getattr(candidate, "id", None)
        )
        if cid is not None and hasattr(cid, "value"):
            cid = cid.value
        if str(cid) == str(step_id):
            step = candidate
            break
    if step is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Step {step_id!r} not found in contract {draft.contract_id}",
        )

    raw_fields = step.get("fields") if isinstance(step, dict) else getattr(step, "fields", None)
    fields_iter: list[Any] = list(raw_fields) if raw_fields is not None else []
    target: Any | None = None
    for field in fields_iter:
        fid: Any = field.get("id") if isinstance(field, dict) else getattr(field, "id", None)
        if fid is not None and hasattr(fid, "value"):
            fid = fid.value
        if str(fid) == field_id:
            target = field
            break
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Field {field_id!r} not found in step {step_id!r}",
        )

    # Synthesize a single-field "step view" so the existing fan-out re-uses
    # all the same blending / budget / metric plumbing.
    synthetic_step = {**step, "fields": [target]} if isinstance(step, dict) else step

    async with PriceFrameClient.from_settings(settings) as priceframe:
        events = await emit_suggestions(
            session,
            run_id=run.id,
            contract=contract,
            step=synthetic_step,
            draft_state=draft.payload,
            auth_ctx=auth,
            priceframe=priceframe,
        )
    await session.commit()
    return {"run_id": run.id, "field_id": field_id, "events_emitted": str(len(events))}


async def _list_proposed_tool_calls_for_step(
    session: AsyncSession,
    *,
    run_id: str,
    step_id: str,
) -> list[AgentToolCall]:
    """Return tool calls awaiting a decision that belong to a given step.

    ``evaluate_step_advance`` tags each bundled call's ``AgentRunStep`` row with
    ``kind='workflow_step:<step_id>'``. We join through ``step_id`` here so a
    run that already had unrelated tool calls from the model loop never gets
    mistakenly approved en bloc.
    """

    kind = f"workflow_step:{step_id}"
    result = await session.execute(
        select(AgentToolCall)
        .join(AgentRunStep, AgentRunStep.id == AgentToolCall.step_id)
        .where(
            AgentToolCall.run_id == run_id,
            AgentToolCall.status.in_(("proposed", "pending", "awaiting_approval")),
            AgentRunStep.kind == kind,
        )
        .order_by(AgentToolCall.created_at.asc())
    )
    return list(result.scalars().all())


@router.get("/runs/{run_id}/stream")
async def stream_run(
    run_id: str,
    request: Request,
    auth: AuthDep,
    settings: SettingsDep,
    last_event_id_header: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> EventSourceResponse:
    session_factory = request.app.state.session_factory
    async with session_factory() as validation_session:
        await require_run(validation_session, auth, run_id)

    query_cursor = request.query_params.get("last_event_id")
    last_event_id = _parse_cursor(query_cursor or last_event_id_header)

    async def generator() -> AsyncIterator[dict[str, str]]:
        emitted_terminal = False
        cursor = last_event_id
        while True:
            async with session_factory() as stream_session:
                events = await list_run_events(
                    stream_session,
                    run_id=run_id,
                    after_seq=cursor,
                    limit=settings.sse_replay_event_limit,
                )
                run = await stream_session.get(AgentRun, run_id)

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
    result: dict[str, Any],
    request: Request,
) -> dict[str, Any]:
    entity, entity_id = _audit_entity(tool_call.tool_name, args, result)
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


def _audit_entity(tool_name: str, args: dict[str, Any], result: dict[str, Any]) -> tuple[str, int]:
    if tool_name in {"update_corridor_pricing", "set_fx_spread"}:
        return "quote_corridor", int(args["corridor_id"])
    if tool_name in {"bulk_add_corridors", "submit_for_approval", "preview_pricing_change"}:
        return "quote", int(args["quote_id"])
    if tool_name == "create_quotation":
        candidate = _result_entity_id(result) or args.get("quote_id") or args.get("id") or 0
        return "quote", int(candidate)
    return "agent_tool_call", 0


def _result_entity_id(result: dict[str, Any]) -> int | None:
    data = result.get("data")
    if isinstance(data, dict):
        nested = data.get("data")
        nested_id = nested.get("id") if isinstance(nested, dict) else None
        if isinstance(nested_id, int):
            return nested_id
        direct_id = data.get("id")
        if isinstance(direct_id, int):
            return direct_id
    return None


def _priceframe_error_status(exc: PriceFrameError) -> int:
    if isinstance(exc, PriceFrameAuthError):
        return status.HTTP_401_UNAUTHORIZED
    if isinstance(exc, PriceFrameForbiddenError):
        return status.HTTP_403_FORBIDDEN
    if isinstance(exc, PriceFrameNotFoundError):
        return status.HTTP_404_NOT_FOUND
    if exc.status_code is not None and exc.status_code < 500:
        return exc.status_code
    return status.HTTP_502_BAD_GATEWAY


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
