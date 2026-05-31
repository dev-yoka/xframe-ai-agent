"""Decide whether to drive create_pricing_request as a conversation, and seed the draft."""
from __future__ import annotations

from typing import Any

from xframe_agent.agent.conversation.cursor import Cursor, set_cursor


def should_start_conversation(*, intent: str | None, flags: dict[str, Any] | None) -> bool:
    if intent != "create_pricing_request":
        return False
    return bool((flags or {}).get("conversation"))


def init_conversation_draft_payload() -> dict[str, Any]:
    return set_cursor({}, Cursor(0, 0))
