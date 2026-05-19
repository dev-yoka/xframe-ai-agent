"""Server-side idempotency helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from xframe_agent.models import AgentIdempotencyKey
from xframe_agent.models.agent import utc_now


async def get_replay(
    session: AsyncSession,
    *,
    user_id: int,
    key: str | None,
) -> AgentIdempotencyKey | None:
    """Return an unexpired idempotency record."""

    if not key:
        return None

    record = await session.get(AgentIdempotencyKey, {"user_id": user_id, "key": key})
    if not record or _aware_utc(record.expires_at) <= utc_now():
        return None
    return record


async def store_replay(
    session: AsyncSession,
    *,
    user_id: int,
    key: str | None,
    resource_kind: str,
    resource_id: str,
    response_payload: dict[str, Any],
    ttl_seconds: int,
) -> None:
    """Persist an idempotency replay record when a key was supplied."""

    if not key:
        return

    session.add(
        AgentIdempotencyKey(
            user_id=user_id,
            key=key,
            resource_kind=resource_kind,
            resource_id=resource_id,
            response_payload=response_payload,
            expires_at=utc_now() + timedelta(seconds=ttl_seconds),
        )
    )


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
