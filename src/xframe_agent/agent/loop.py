"""Single-agent run loop.

The loop is still deterministic when no LLM provider is configured. When a
provider is available the model-orchestrated path (:mod:`agent.runner`) takes
over. Either way, this module is responsible for:

* writing durable :class:`AgentRunStep` rows alongside the SSE event log,
* enforcing per-run ceilings via :class:`LoopBudget`,
* honoring each tool's ``await tool.requires_approval(...)`` policy,
* applying PII redaction and tool-output wrapping before any provider call.
"""

from __future__ import annotations

import json
from time import perf_counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from xframe_agent.agent.budget import BudgetExceededError, LoopBudget
from xframe_agent.agent.events import append_run_event
from xframe_agent.agent.guided_workflows import (
    CREATE_PRICING_REQUEST_CONTRACT_VERSION,
    CREATE_PRICING_REQUEST_INITIAL_STEP,
    CREATE_PRICING_REQUEST_WORKFLOW,
    create_pricing_request_input_payload,
    create_pricing_request_step_entered_payload,
    is_generic_create_pricing_request,
    parse_create_pricing_request_submission,
)
from xframe_agent.agent.redaction import redact
from xframe_agent.agent.suggestions import emit_historical_suggestions
from xframe_agent.agent.workflow_advance import (
    StepAdvanceError,
    get_step,
    load_contract,
)
from xframe_agent.auth.jwt import AuthContext
from xframe_agent.models import (
    AgentConversation,
    AgentMessage,
    AgentRun,
    AgentRunStep,
    AgentToolCall,
    AgentUserMemory,
)
from xframe_agent.models.agent import utc_now
from xframe_agent.observability.metrics import observe_run_latency, observe_step_count
from xframe_agent.priceframe import PriceFrameClient
from xframe_agent.settings import Settings, get_settings
from xframe_agent.tools.base import ToolDefinition
from xframe_agent.tools.registry import tool_registry


class AgentLoop:
    """Deterministic + provider-driven run loop."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    async def run(self, session: AsyncSession, *, run_id: str, context: AuthContext) -> AgentRun:
        started = perf_counter()
        run = await session.get(AgentRun, run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        if run.status == "cancelled":
            return run

        run.status = "running"
        run.started_at = utc_now()
        run.updated_at = utc_now()
        await append_run_event(session, run_id=run.id, event_type="v1.run.started")

        budget = LoopBudget(settings=self._settings)
        try:
            budget.begin_step()
        except BudgetExceededError as exc:
            await self._finalize_error(session, run=run, budget=budget, exc=exc)
            await session.commit()
            return run

        step_record = await self._open_step(session, run_id=run.id, seq=budget.steps, kind="model")
        await append_run_event(
            session,
            run_id=run.id,
            event_type="v1.step.started",
            payload={"step": budget.steps, "kind": "model"},
        )

        user_message = await self._input_message(session, run)
        redacted = redact(user_message.content)
        conversation = await session.get(AgentConversation, run.conversation_id)
        is_create_pricing_conversation = (
            conversation is not None and conversation.kind == CREATE_PRICING_REQUEST_WORKFLOW
        )
        if is_create_pricing_conversation:
            try:
                workflow_args = parse_create_pricing_request_submission(redacted.text)
            except (ValueError, json.JSONDecodeError) as exc:
                await self._close_step(step_record, status="error")
                await self._finalize_workflow_error(session, run=run, exc=exc)
                await session.commit()
                return run
            if workflow_args is not None:
                await self._close_step(step_record, status="completed")
                await append_run_event(
                    session,
                    run_id=run.id,
                    event_type="v1.step.completed",
                    payload={"step": budget.steps, "kind": "model"},
                )
                return await self._propose_tool_call(
                    session,
                    run=run,
                    context=context,
                    budget=budget,
                    tool_name="create_quotation",
                    args=workflow_args,
                    model_name="deterministic-guided-workflow",
                    started=started,
                )

        is_create_pricing_wizard = (
            is_create_pricing_conversation
            and is_generic_create_pricing_request(redacted.text)
        )
        proposal = self._build_tool_proposal(redacted.text, context)
        assistant_text = (
            "Let's set up the pricing request. I filled the basics so you can adjust "
            "only what matters."
            if is_create_pricing_wizard
            else self._deterministic_response(redacted.text, context, proposal)
        )
        assistant_message = AgentMessage(
            conversation_id=run.conversation_id,
            user_id=context.user_id,
            role="assistant",
            content=assistant_text,
            source="agent",
            run_id=run.id,
        )
        session.add(assistant_message)
        await session.flush()

        await append_run_event(
            session,
            run_id=run.id,
            event_type="v1.message.delta",
            payload={"message_id": assistant_message.id, "delta": assistant_text},
        )
        await self._close_step(step_record, status="completed")
        await append_run_event(
            session,
            run_id=run.id,
            event_type="v1.step.completed",
            payload={"step": budget.steps, "kind": "model"},
        )
        await self._summarize_memory(
            session,
            run=run,
            content=redacted.text,
            context=context,
        )

        if is_create_pricing_wizard:
            run.output_message_id = assistant_message.id
            run.status = "completed"
            run.completed_at = utc_now()
            run.updated_at = utc_now()
            await append_run_event(
                session,
                run_id=run.id,
                event_type="v1.workflow.step.entered",
                payload=create_pricing_request_step_entered_payload(),
            )
            await self._fanout_initial_step_suggestions(
                session,
                run_id=run.id,
                context=context,
            )
            await append_run_event(
                session,
                run_id=run.id,
                event_type="v1.input.requested",
                payload=create_pricing_request_input_payload(),
            )
            await append_run_event(
                session,
                run_id=run.id,
                event_type="v1.run.completed",
                payload={"workflow": CREATE_PRICING_REQUEST_WORKFLOW},
            )
            observe_step_count(budget.steps)
            observe_run_latency(model="deterministic-phase-f", seconds=perf_counter() - started)
            await session.commit()
            return run

        if proposal is not None:
            return await self._propose_tool_call(
                session,
                run=run,
                context=context,
                budget=budget,
                tool_name=proposal["name"],
                args=proposal["args"],
                model_name="deterministic-phase-e",
                started=started,
                output_message_id=assistant_message.id,
            )

        run.output_message_id = assistant_message.id
        run.status = "completed"
        run.completed_at = utc_now()
        run.updated_at = utc_now()
        await append_run_event(
            session,
            run_id=run.id,
            event_type="v1.run.completed",
            payload={"message_id": assistant_message.id, "budget": budget.snapshot()},
        )
        observe_step_count(budget.steps)
        observe_run_latency(model="deterministic-phase-d", seconds=perf_counter() - started)
        await session.commit()
        return run

    async def _input_message(self, session: AsyncSession, run: AgentRun) -> AgentMessage:
        result = await session.execute(
            select(AgentMessage).where(AgentMessage.id == run.input_message_id)
        )
        return result.scalar_one()

    @staticmethod
    def _deterministic_response(
        content: str,
        context: AuthContext,
        proposal: dict[str, Any] | None = None,
    ) -> str:
        if proposal is not None:
            return (
                f"I prepared `{proposal['name']}` for approval. "
                "Review the proposed tool call before it is executed."
            )
        return (
            "I received your pricing request and created a durable run. "
            f"Available permissions for this session: {', '.join(context.permissions)}. "
            f"Request: {content}"
        )

    def _build_tool_proposal(
        self,
        content: str,
        context: AuthContext,
    ) -> dict[str, Any] | None:
        parsed = _extract_tool_directive(content)
        if parsed is None:
            return None
        tool = tool_registry.get(parsed["name"])
        if tool is None or tool.risk == "READ" or not context.has_permission(tool.permission):
            return None
        try:
            args = tool.input_model.model_validate(parsed["args"]).model_dump(mode="json")
        except ValueError:
            return None
        return {"name": tool.name, "args": args}

    async def _summarize_memory(
        self,
        session: AsyncSession,
        *,
        run: AgentRun,
        content: str,
        context: AuthContext,
    ) -> None:
        memory = _extract_memory(content)
        if memory is None:
            return
        session.add(
            AgentUserMemory(
                user_id=context.user_id,
                key="user_note",
                value=memory,
                source="summarizer",
                extra_metadata={"run_id": run.id},
            )
        )
        await append_run_event(
            session,
            run_id=run.id,
            event_type="v1.memory.updated",
            payload={"key": "user_note"},
        )

    async def _open_step(
        self,
        session: AsyncSession,
        *,
        run_id: str,
        seq: int,
        kind: str,
    ) -> AgentRunStep:
        step = AgentRunStep(run_id=run_id, seq=seq, kind=kind, status="running")
        session.add(step)
        await session.flush()
        return step

    async def _close_step(self, step: AgentRunStep, *, status: str) -> None:
        step.status = status
        step.completed_at = utc_now()

    async def _propose_tool_call(
        self,
        session: AsyncSession,
        *,
        run: AgentRun,
        context: AuthContext,
        budget: LoopBudget,
        tool_name: str,
        args: dict[str, Any],
        model_name: str,
        started: float,
        output_message_id: str | None = None,
    ) -> AgentRun:
        try:
            budget.record_tool_call()
        except BudgetExceededError as exc:
            await self._finalize_error(session, run=run, budget=budget, exc=exc)
            await session.commit()
            return run

        tool = tool_registry.get(tool_name)
        if tool is None or not context.has_permission(tool.permission):
            await self._finalize_tool_error(
                session,
                run=run,
                cause="tool_unavailable",
                message=f"Tool '{tool_name}' is not available to this user.",
            )
            await session.commit()
            return run

        parsed_args = tool.input_model.model_validate(args)
        requires_approval = await tool.requires_approval(parsed_args, context)
        tool_call_step = await self._open_step(
            session,
            run_id=run.id,
            seq=budget.steps,
            kind="tool_call",
        )
        tool_call = AgentToolCall(
            run_id=run.id,
            tool_name=tool.name,
            status="proposed" if requires_approval else "pending",
            args=parsed_args.model_dump(mode="json"),
            requires_approval=requires_approval,
            step_id=tool_call_step.id,
        )
        session.add(tool_call)
        await session.flush()
        run.output_message_id = output_message_id
        run.status = "awaiting_decision" if requires_approval else "running"
        run.updated_at = utc_now()
        await append_run_event(
            session,
            run_id=run.id,
            event_type="v1.tool.proposed",
            payload={
                "tool_call_id": tool_call.id,
                "tool_name": tool_call.tool_name,
                "args": tool_call.args,
                "requires_approval": requires_approval,
            },
        )
        if requires_approval:
            await append_run_event(
                session,
                run_id=run.id,
                event_type="v1.run.awaiting_decision",
                payload={"tool_call_id": tool_call.id},
            )
            await self._close_step(tool_call_step, status="awaiting_decision")
            observe_step_count(budget.steps)
            observe_run_latency(model=model_name, seconds=perf_counter() - started)
            await session.commit()
            return run

        # Auto-approve path: tool's policy says no human gate needed.
        # Execution itself still happens through the decisions endpoint or
        # an arq worker; for the in-loop deterministic path we leave the
        # tool call in "pending" so the existing decisions endpoint can
        # finish it (preserving the audit and idempotency story).
        await self._close_step(tool_call_step, status="pending")
        run.status = "awaiting_decision"
        run.updated_at = utc_now()
        await append_run_event(
            session,
            run_id=run.id,
            event_type="v1.run.awaiting_decision",
            payload={"tool_call_id": tool_call.id, "auto_approved": True},
        )
        observe_step_count(budget.steps)
        observe_run_latency(model=model_name, seconds=perf_counter() - started)
        await session.commit()
        return run

    async def _finalize_error(
        self,
        session: AsyncSession,
        *,
        run: AgentRun,
        budget: LoopBudget,
        exc: BudgetExceededError,
    ) -> None:
        run.status = "error"
        run.error = str(exc)
        run.completed_at = utc_now()
        run.updated_at = utc_now()
        await append_run_event(
            session,
            run_id=run.id,
            event_type="v1.run.error",
            payload={"cause": exc.cause, "message": str(exc), "budget": budget.snapshot()},
        )

    async def _finalize_workflow_error(
        self,
        session: AsyncSession,
        *,
        run: AgentRun,
        exc: Exception,
    ) -> None:
        await self._finalize_tool_error(
            session,
            run=run,
            cause="workflow_payload_invalid",
            message=str(exc),
        )

    async def _finalize_tool_error(
        self,
        session: AsyncSession,
        *,
        run: AgentRun,
        cause: str,
        message: str,
    ) -> None:
        run.status = "error"
        run.error = message
        run.completed_at = utc_now()
        run.updated_at = utc_now()
        await append_run_event(
            session,
            run_id=run.id,
            event_type="v1.run.error",
            payload={"cause": cause, "message": message},
        )

    async def _fanout_initial_step_suggestions(
        self,
        session: AsyncSession,
        *,
        run_id: str,
        context: AuthContext,
    ) -> None:
        """Fan out proactive-historical suggestions for the initial wizard step.

        Best-effort: any failure (missing contract, network error, etc.) is
        swallowed so the workflow always advances. Builds a single short-lived
        ``PriceFrameClient`` because the deterministic loop owns no shared
        client (unlike :class:`ModelRunner`).
        """

        try:
            contract = load_contract(
                CREATE_PRICING_REQUEST_WORKFLOW,
                CREATE_PRICING_REQUEST_CONTRACT_VERSION,
            )
        except StepAdvanceError:
            return
        step = get_step(contract, CREATE_PRICING_REQUEST_INITIAL_STEP)
        if step is None:
            return
        try:
            async with PriceFrameClient.from_settings(self._settings) as priceframe:
                await emit_historical_suggestions(
                    session,
                    run_id=run_id,
                    contract=contract,
                    step=step,
                    draft_state={},
                    auth_ctx=context,
                    priceframe=priceframe,
                )
        except Exception:  # noqa: BLE001 - suggestions are best-effort
            return


def _extract_memory(content: str) -> str | None:
    marker = "remember that "
    normalized = content.strip()
    index = normalized.lower().find(marker)
    if index < 0:
        return None
    memory = normalized[index + len(marker) :].strip()
    return memory[:1000] if memory else None


def _extract_tool_directive(content: str) -> dict[str, Any] | None:
    normalized = content.strip()
    if not normalized.lower().startswith("tool:"):
        return None
    raw = normalized.split(":", 1)[1].strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    name = payload.get("name")
    args = payload.get("args")
    if not isinstance(name, str) or not isinstance(args, dict):
        return None
    return {"name": name, "args": args}


__all__ = ["AgentLoop", "ToolDefinition"]
