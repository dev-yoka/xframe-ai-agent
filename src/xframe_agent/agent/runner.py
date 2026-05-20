"""Model-orchestrated run loop driven by a provider failover router.

When at least one :class:`~xframe_agent.provider.base.Provider` is configured,
:class:`ModelRunner` walks the conversation through the provider's streaming
tool-call protocol. It enforces the same budget ceilings as the deterministic
loop, emits the same SSE event taxonomy, and pauses cleanly when a write tool
needs human approval.

Reads are dispatched in parallel via :func:`asyncio.gather` up to
``settings.max_parallel_tool_calls``. Writes are always serial — two concurrent
``update_corridor_pricing`` calls would race PriceFRAME's change-history append.
Repeated identical tool calls (3x in a row) trip the loop-detection guard.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from xframe_agent.agent.budget import BudgetExceededError, LoopBudget
from xframe_agent.agent.events import append_run_event
from xframe_agent.agent.prompts.create_pricing_request import get_system_prompt
from xframe_agent.agent.redaction import redact
from xframe_agent.agent.wrapping import wrap_tool_output
from xframe_agent.auth.jwt import AuthContext
from xframe_agent.models import (
    AgentConversation,
    AgentMessage,
    AgentRun,
    AgentRunStep,
    AgentToolCall,
)
from xframe_agent.models.agent import utc_now
from xframe_agent.priceframe import PriceFrameClient
from xframe_agent.provider.base import (
    ChatMessage,
    ContentBlock,
    ProviderError,
    ProviderFailoverRouter,
    StreamEvent,
)
from xframe_agent.settings import Settings
from xframe_agent.tools.base import ToolDefinition
from xframe_agent.tools.registry import tool_registry


class LoopDetectedError(Exception):
    """Raised when the same tool+args have been requested 3 times in a row."""


@dataclass(slots=True)
class ProposedCall:
    name: str
    args: dict[str, Any]
    call_id: str


@dataclass(slots=True)
class ToolExecutionResult:
    proposal: ProposedCall
    output: dict[str, Any]
    risk: str


class ModelRunner:
    """Driver for the provider-backed agent loop."""

    def __init__(
        self,
        *,
        router: ProviderFailoverRouter,
        settings: Settings,
        model: str,
        priceframe_factory: PriceFrameClient | None = None,
    ) -> None:
        self._router = router
        self._settings = settings
        self._model = model
        self._priceframe = priceframe_factory

    async def run(
        self,
        session: AsyncSession,
        *,
        run: AgentRun,
        context: AuthContext,
        history: Sequence[ChatMessage],
    ) -> AgentRun:
        budget = LoopBudget(settings=self._settings)
        messages = list(history)
        tools = list(tool_registry.available_for(context))
        recent: list[tuple[str, str]] = []

        # Inject system prompt for guided flows or when no history context exists
        conversation = await session.get(AgentConversation, run.conversation_id)
        conv_kind = (conversation.kind if conversation else None) or "general"
        if conv_kind == "create_pricing_request" or not messages:
            system_msg = ChatMessage(
                role="system",
                content=[
                    ContentBlock(
                        type="text",
                        payload={
                            "text": get_system_prompt(
                                role_code=context.role_code,
                                profile_code=context.profile_code,
                                permissions=context.permissions,
                            )
                        },
                    )
                ],
            )
            messages = [system_msg] + messages

        try:
            while True:
                budget.begin_step()
                step = await self._open_step(
                    session,
                    run_id=run.id,
                    seq=budget.steps,
                    kind="model_call",
                )
                await append_run_event(
                    session,
                    run_id=run.id,
                    event_type="v1.step.started",
                    payload={"step": budget.steps, "kind": "model_call"},
                )

                proposals, assistant_text, usage = await self._call_provider(
                    messages=messages,
                    tools=tools,
                    budget=budget,
                )

                if assistant_text:
                    redacted = redact(assistant_text)
                    msg = AgentMessage(
                        conversation_id=run.conversation_id,
                        user_id=context.user_id,
                        role="assistant",
                        content=redacted.text,
                        source="agent",
                        run_id=run.id,
                    )
                    session.add(msg)
                    await session.flush()
                    await append_run_event(
                        session,
                        run_id=run.id,
                        event_type="v1.message.delta",
                        payload={"message_id": msg.id, "delta": redacted.text},
                    )
                    run.output_message_id = msg.id

                await self._close_step(step, status="completed")
                await append_run_event(
                    session,
                    run_id=run.id,
                    event_type="v1.step.completed",
                    payload={"step": budget.steps, "kind": "model_call", "usage": usage},
                )

                if not proposals:
                    run.status = "completed"
                    run.completed_at = utc_now()
                    run.updated_at = utc_now()
                    await append_run_event(
                        session,
                        run_id=run.id,
                        event_type="v1.run.completed",
                        payload={"budget": budget.snapshot()},
                    )
                    await session.commit()
                    return run

                # Loop detection: same tool+args 3x in a row aborts.
                for call in proposals:
                    signature = (call.name, json.dumps(call.args, sort_keys=True))
                    recent.append(signature)
                    recent[:] = recent[-3:]
                    if len(recent) == 3 and len(set(recent)) == 1:
                        raise LoopDetectedError(call.name)

                tool_results, paused = await self._dispatch_proposals(
                    session,
                    run=run,
                    context=context,
                    budget=budget,
                    proposals=proposals,
                )
                if paused:
                    await session.commit()
                    return run

                for result in tool_results:
                    messages.append(
                        ChatMessage(
                            role="tool",
                            content=[
                                ContentBlock(
                                    type="tool_result",
                                    payload={
                                        "tool_call_id": result.proposal.call_id,
                                        "wrapped": wrap_tool_output(
                                            tool_name=result.proposal.name,
                                            call_id=result.proposal.call_id,
                                            payload=result.output,
                                        ),
                                    },
                                )
                            ],
                        )
                    )

        except BudgetExceededError as exc:
            await self._finalize_error(
                session, run=run, budget=budget, cause=exc.cause, message=str(exc)
            )
            await session.commit()
            return run
        except LoopDetectedError as exc:
            await self._finalize_error(
                session,
                run=run,
                budget=budget,
                cause="loop_detected",
                message=f"loop detected on tool {exc!s}",
            )
            await session.commit()
            return run
        except ProviderError as exc:
            await self._finalize_error(
                session,
                run=run,
                budget=budget,
                cause="provider_error",
                message=str(exc),
            )
            await session.commit()
            return run

    async def _call_provider(
        self,
        *,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDefinition[Any, Any]],
        budget: LoopBudget,
    ) -> tuple[list[ProposedCall], str, dict[str, int]]:
        proposals: list[ProposedCall] = []
        assistant_text = ""
        usage = {"input_tokens": 0, "output_tokens": 0}

        async for event in self._router.stream(
            messages,
            tools,
            model=self._model,
            max_output_tokens=self._settings.max_output_tokens_per_run,
        ):
            assistant_text, usage = _consume_event(event, proposals, assistant_text, usage)

        budget.record_usage(
            model=self._model,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
        )
        return proposals, assistant_text, usage

    async def _dispatch_proposals(
        self,
        session: AsyncSession,
        *,
        run: AgentRun,
        context: AuthContext,
        budget: LoopBudget,
        proposals: Iterable[ProposedCall],
    ) -> tuple[list[ToolExecutionResult], bool]:
        proposal_list = list(proposals)
        readers: list[tuple[ProposedCall, ToolDefinition[Any, Any], AgentToolCall, Any]] = []
        writers: list[tuple[ProposedCall, ToolDefinition[Any, Any], AgentToolCall]] = []

        for proposal in proposal_list:
            tool = tool_registry.get(proposal.name)
            if tool is None:
                await append_run_event(
                    session,
                    run_id=run.id,
                    event_type="v1.tool.error",
                    payload={"cause": "unknown_tool", "name": proposal.name},
                )
                continue
            try:
                parsed = tool.input_model.model_validate(proposal.args)
            except ValueError as exc:
                await append_run_event(
                    session,
                    run_id=run.id,
                    event_type="v1.tool.error",
                    payload={
                        "cause": "schema_validation_failed",
                        "name": proposal.name,
                        "detail": str(exc),
                    },
                )
                continue

            budget.record_tool_call()
            requires_approval = await tool.requires_approval(parsed, context)
            step = await self._open_step(
                session,
                run_id=run.id,
                seq=budget.steps,
                kind="tool_call",
            )
            tool_call = AgentToolCall(
                run_id=run.id,
                tool_name=tool.name,
                status="proposed" if requires_approval else "pending",
                args=parsed.model_dump(mode="json"),
                requires_approval=requires_approval,
                step_id=step.id,
            )
            session.add(tool_call)
            await session.flush()
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
                await self._close_step(step, status="awaiting_decision")
                run.status = "awaiting_decision"
                run.updated_at = utc_now()
                await append_run_event(
                    session,
                    run_id=run.id,
                    event_type="v1.run.awaiting_decision",
                    payload={"tool_call_id": tool_call.id},
                )
                return [], True

            if tool.risk == "READ":
                readers.append((proposal, tool, tool_call, parsed))
            else:
                writers.append((proposal, tool, tool_call))

        results: list[ToolExecutionResult] = []
        if readers:
            sem = asyncio.Semaphore(self._settings.max_parallel_tool_calls)

            async def _exec_read(
                proposal: ProposedCall,
                tool: ToolDefinition[Any, Any],
                record: AgentToolCall,
                parsed: Any,
            ) -> ToolExecutionResult:
                async with sem:
                    return await self._execute_one(
                        proposal=proposal,
                        tool=tool,
                        record=record,
                        parsed=parsed,
                        context=context,
                        session=session,
                    )

            done = await asyncio.gather(
                *(_exec_read(p, t, r, a) for p, t, r, a in readers),
                return_exceptions=False,
            )
            results.extend(done)
        # Writers serial (writes shouldn't auto-execute without approval anyway)
        for proposal, tool, record in writers:
            parsed = tool.input_model.model_validate(record.args)
            result = await self._execute_one(
                proposal=proposal,
                tool=tool,
                record=record,
                parsed=parsed,
                context=context,
                session=session,
            )
            results.append(result)
        return results, False

    async def _execute_one(
        self,
        *,
        proposal: ProposedCall,
        tool: ToolDefinition[Any, Any],
        record: AgentToolCall,
        parsed: Any,
        context: AuthContext,
        session: AsyncSession,
    ) -> ToolExecutionResult:
        if self._priceframe is None:
            raise ProviderError("PriceFrameClient is required to execute tools")
        await append_run_event(
            session,
            run_id=record.run_id,
            event_type="v1.tool.started",
            payload={"tool_call_id": record.id, "tool_name": tool.name},
        )
        result_model = await tool.execute(parsed, context, self._priceframe)
        dumped = result_model.model_dump(mode="json")
        projected = tool.project_for_model(dumped)
        record.status = "succeeded"
        record.result = dumped
        record.completed_at = utc_now()
        await append_run_event(
            session,
            run_id=record.run_id,
            event_type="v1.tool.completed",
            payload={"tool_call_id": record.id, "result": projected},
        )
        return ToolExecutionResult(proposal=proposal, output=projected, risk=tool.risk)

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

    async def _finalize_error(
        self,
        session: AsyncSession,
        *,
        run: AgentRun,
        budget: LoopBudget,
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
            payload={"cause": cause, "message": message, "budget": budget.snapshot()},
        )


def _consume_event(
    event: StreamEvent,
    proposals: list[ProposedCall],
    text: str,
    usage: dict[str, int],
) -> tuple[str, dict[str, int]]:
    if event.kind == "text_delta":
        text = text + str(event.payload.get("delta", ""))
    elif event.kind == "tool_use":
        proposals.append(
            ProposedCall(
                name=str(event.payload["name"]),
                args=dict(event.payload.get("args", {})),
                call_id=str(event.payload.get("call_id", "")),
            )
        )
    elif event.kind == "usage":
        usage = {
            "input_tokens": int(event.payload.get("input_tokens", usage["input_tokens"])),
            "output_tokens": int(event.payload.get("output_tokens", usage["output_tokens"])),
        }
    return text, usage


__all__ = ["ModelRunner", "LoopDetectedError", "ProposedCall"]
