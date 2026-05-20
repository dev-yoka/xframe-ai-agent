"""Load conversation history into provider-agnostic ``ChatMessage`` lists."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from xframe_agent.models import AgentMessage
from xframe_agent.provider.base import ChatMessage, ContentBlock


async def load_history(
    session: AsyncSession,
    *,
    conversation_id: str,
    limit: int = 50,
) -> list[ChatMessage]:
    """Load the most recent messages for a conversation, oldest first.

    Only ``user`` and ``assistant`` messages are returned; ``tool`` and
    ``system`` rows are skipped because the runner reconstructs them per-run
    (system prompt is injected; tool results are re-fetched live).
    """

    result = await session.execute(
        select(AgentMessage)
        .where(AgentMessage.conversation_id == conversation_id)
        .order_by(AgentMessage.created_at.desc())
        .limit(limit)
    )
    rows = list(result.scalars().all())
    rows.reverse()  # oldest-first for chronological history

    history: list[ChatMessage] = []
    for row in rows:
        if row.role not in {"user", "assistant"}:
            continue
        history.append(
            ChatMessage(
                role=row.role,
                content=[ContentBlock(type="text", payload={"text": row.content})],
            )
        )
    return history


__all__ = ["load_history"]
