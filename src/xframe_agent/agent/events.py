"""Run event persistence and SSE formatting."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from xframe_agent.models import AgentRunEvent
from xframe_agent.observability.metrics import observe_workflow_step_duration

# M2.1.B — Wizard step proposal event names. The agent emits ``...step.proposed``
# when entering a step with declared ``essential_field_ids``; the wizard then
# echoes the user's decision back via the step_proposal_decision endpoint which
# emits the matching ``...proposal_accepted`` or ``...proposal_dismissed`` event.
EVENT_WORKFLOW_STEP_PROPOSED = "v1.workflow.step.proposed"
EVENT_WORKFLOW_STEP_PROPOSAL_ACCEPTED = "v1.workflow.step.proposal_accepted"
EVENT_WORKFLOW_STEP_PROPOSAL_DISMISSED = "v1.workflow.step.proposal_dismissed"


async def append_run_event(
    session: AsyncSession,
    *,
    run_id: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> AgentRunEvent:
    """Append a monotonic event for a run."""

    next_seq_result = await session.execute(
        select(func.coalesce(func.max(AgentRunEvent.seq), 0) + 1).where(
            AgentRunEvent.run_id == run_id
        )
    )
    seq = int(next_seq_result.scalar_one())
    event = AgentRunEvent(
        run_id=run_id,
        seq=seq,
        event_type=event_type,
        payload=payload or {},
    )
    session.add(event)
    await session.flush()
    return event


async def list_run_events(
    session: AsyncSession,
    *,
    run_id: str,
    after_seq: int = 0,
    limit: int = 2000,
) -> list[AgentRunEvent]:
    """Return durable events after a resume cursor."""

    result = await session.execute(
        select(AgentRunEvent)
        .where(AgentRunEvent.run_id == run_id, AgentRunEvent.seq > after_seq)
        .order_by(AgentRunEvent.seq.asc())
        .limit(limit)
    )
    return list(result.scalars().all())


def event_payload(event: AgentRunEvent) -> dict[str, Any]:
    """Return the versioned event payload sent to clients."""

    return {
        "run_id": event.run_id,
        "seq": event.seq,
        "ts": _aware_utc(event.created_at).isoformat(),
        **event.payload,
    }


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def record_step_duration_from_events(
    session: AsyncSession,
    *,
    run_id: str,
    step_id: str,
    outcome: str,
) -> float | None:
    """Record the wall-clock duration of a workflow step on a terminal event.

    Looks up the most recent ``v1.workflow.step.entered`` event for this run +
    step_id and observes ``now - that event's created_at`` against the
    ``agent_workflow_step_duration_seconds`` histogram. Returns the recorded
    duration in seconds, or ``None`` when no matching enter event exists (the
    step was never entered or events were purged) — in which case the metric
    is skipped to avoid polluting the histogram with synthetic samples.

    Outcome should be one of ``approved``, ``blocked``, ``rejected``,
    ``abandoned`` — the metric helper clamps unknown values to ``abandoned``.
    """

    stmt = (
        select(AgentRunEvent)
        .where(
            AgentRunEvent.run_id == run_id,
            AgentRunEvent.event_type == "v1.workflow.step.entered",
        )
        .order_by(desc(AgentRunEvent.created_at), desc(AgentRunEvent.seq))
        .limit(8)
    )
    result = await session.execute(stmt)
    candidate: AgentRunEvent | None = None
    for event in result.scalars():
        payload = event.payload or {}
        if payload.get("step_id") == step_id:
            candidate = event
            break
    if candidate is None:
        return None
    entered_at = _aware_utc(candidate.created_at)
    now = datetime.now(UTC)
    seconds = max(0.0, (now - entered_at).total_seconds())
    observe_workflow_step_duration(step_id=step_id, outcome=outcome, seconds=seconds)
    return seconds
