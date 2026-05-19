"""Single-agent run loop skeleton for Phase D."""

from __future__ import annotations

from time import perf_counter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from xframe_agent.agent.events import append_run_event
from xframe_agent.auth.jwt import AuthContext
from xframe_agent.models import AgentMessage, AgentRun
from xframe_agent.models.agent import utc_now
from xframe_agent.observability.metrics import observe_run_latency, observe_step_count


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
        assistant_text = self._deterministic_response(user_message.content, context)
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
    def _deterministic_response(content: str, context: AuthContext) -> str:
        return (
            "I received your pricing request and created a durable run. "
            f"Available permissions for this session: {', '.join(context.permissions)}. "
            f"Request: {content}"
        )
