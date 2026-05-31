"""Payload builders for conversational SSE events (v1.field.*, v1.conversation.*)."""
from __future__ import annotations

from typing import Any

from xframe_agent.agent.conversation.sequencer import FieldPrompt

EVENT_FIELD_PROMPT = "v1.field.prompt"
EVENT_FIELD_ACCEPTED = "v1.field.accepted"
EVENT_CONVERSATION_RECAP = "v1.conversation.recap"
EVENT_CONVERSATION_COMMITTED = "v1.conversation.committed"


def field_prompt_payload(
    prompt: FieldPrompt, suggestion: dict[str, Any] | None
) -> dict[str, Any]:
    return {
        "phase_id": prompt.phase_id,
        "field_id": prompt.field_id,
        "label": prompt.label,
        "type": prompt.type,
        "control": prompt.control,
        "required": prompt.required,
        "requires_explicit_confirm": prompt.requires_explicit_confirm,
        "options": prompt.options,
        "options_source": prompt.options_source,
        "ui": prompt.ui,
        "suggestion": suggestion,
    }


def field_accepted_payload(field_id: str, value: Any) -> dict[str, Any]:
    return {"field_id": field_id, "value": value}


def recap_payload(
    collected: dict[str, Any], defaulted: dict[str, Any]
) -> dict[str, Any]:
    return {"collected": collected, "defaulted": defaulted}


def committed_payload(
    quote_id: str,
    applied: list[str],
    failed: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {"quote_id": quote_id, "applied": applied, "failed": failed or []}
