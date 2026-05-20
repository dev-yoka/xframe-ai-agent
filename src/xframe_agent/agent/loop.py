"""Single-agent run loop skeleton for Phase D."""

from __future__ import annotations

import json
from time import perf_counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from xframe_agent.agent.events import append_run_event
from xframe_agent.auth.jwt import AuthContext
from xframe_agent.models import AgentMessage, AgentRun, AgentToolCall, AgentUserMemory
from xframe_agent.models.agent import utc_now
from xframe_agent.observability.metrics import observe_run_latency, observe_step_count
from xframe_agent.tools.registry import tool_registry


class AgentLoop:
    """Small deterministic loop until provider/tool orchestration is expanded."""

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
        await append_run_event(
            session,
            run_id=run.id,
            event_type="v1.step.started",
            payload={"step": 1, "kind": "model"},
        )

        user_message = await self._input_message(session, run)
        proposal = self._build_tool_proposal(user_message.content, context)
        assistant_text = self._deterministic_response(user_message.content, context, proposal)
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
        await append_run_event(
            session,
            run_id=run.id,
            event_type="v1.step.completed",
            payload={"step": 1, "kind": "model"},
        )
        await self._summarize_memory(
            session,
            run=run,
            content=user_message.content,
            context=context,
        )

        if proposal is not None:
            tool_call = AgentToolCall(
                run_id=run.id,
                tool_name=proposal["name"],
                status="proposed",
                args=proposal["args"],
                requires_approval=True,
            )
            session.add(tool_call)
            await session.flush()
            run.output_message_id = assistant_message.id
            run.status = "awaiting_decision"
            run.updated_at = utc_now()
            await append_run_event(
                session,
                run_id=run.id,
                event_type="v1.tool.proposed",
                payload={
                    "tool_call_id": tool_call.id,
                    "tool_name": tool_call.tool_name,
                    "args": tool_call.args,
                },
            )
            await append_run_event(
                session,
                run_id=run.id,
                event_type="v1.run.awaiting_decision",
                payload={"tool_call_id": tool_call.id},
            )
            observe_step_count(1)
            observe_run_latency(model="deterministic-phase-e", seconds=perf_counter() - started)
            await session.commit()
            return run

        run.output_message_id = assistant_message.id
        run.status = "completed"
        run.completed_at = utc_now()
        run.updated_at = utc_now()
        await append_run_event(
            session,
            run_id=run.id,
            event_type="v1.run.completed",
            payload={"message_id": assistant_message.id},
        )
        observe_step_count(1)
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
