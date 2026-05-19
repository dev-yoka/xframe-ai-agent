"""Run event persistence and SSE formatting."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from xframe_agent.models import AgentRunEvent


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
