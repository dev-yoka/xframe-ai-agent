"""On-demand LLM assist: parse free-text, answer 'why', or navigate. Bounded + degradable."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AssistAction:
    action: str  # "set_field" | "explain" | "navigate"
    field_id: str | None = None
    value: Any = None
    text: str | None = None
    degraded: bool = False


async def _call_llm(prompt: str, **kw: Any) -> str:
    raise NotImplementedError  # wired in integration; unit tests monkeypatch this


async def interpret_freeform(
    text: str, *, current_field_id: str | None, contract: dict[str, Any]
) -> AssistAction:
    prompt = (
        "You route a user's chat reply during a structured pricing-request form.\n"
        f"Current field: {current_field_id}. Reply JSON only with keys "
        '{action: set_field|explain|navigate, field_id?, value?, text?}.\n'
        f"User said: {text!r}"
    )
    try:
        raw = await _call_llm(prompt)
        data = json.loads(raw)
        return AssistAction(
            action=data.get("action", "explain"),
            field_id=data.get("field_id"),
            value=data.get("value"),
            text=data.get("text"),
        )
    except Exception:  # noqa: BLE001 — degrade to deterministic path
        return AssistAction(
            action="explain",
            text="Let's keep going — you can pick a value below.",
            degraded=True,
        )
