"""On-demand LLM assist: parse free-text, answer 'why', or navigate. Bounded + degradable."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from xframe_agent.settings import Settings


@dataclass(frozen=True)
class AssistAction:
    action: str  # "set_field" | "explain" | "navigate"
    field_id: str | None = None
    value: Any = None
    text: str | None = None
    degraded: bool = False


async def _call_llm(prompt: str, **kw: Any) -> str:
    """Issue a single-turn text prompt through the provider failover router.

    Builds a minimal ``[{role: user, content: prompt}]`` messages list, streams
    all ``text_delta`` events from the first available healthy provider, and
    returns the concatenated string response.

    Accepts an optional ``settings`` keyword argument so callers (or tests) can
    inject a pre-built :class:`~xframe_agent.settings.Settings` instance.  When
    not supplied, ``Settings()`` is constructed lazily from the environment.

    Unit tests monkeypatch this function directly — the raise/implementation is
    transparent to them.
    """
    from xframe_agent.provider.base import ChatMessage, ContentBlock
    from xframe_agent.provider.factory import build_router
    from xframe_agent.settings import Settings

    settings: Settings = kw.get("settings") or Settings()
    router = build_router(settings)
    if router is None:
        raise RuntimeError("No LLM provider is configured")

    messages = [
        ChatMessage(
            role="user",
            content=[ContentBlock(type="text", payload={"text": prompt})],
        )
    ]

    chunks: list[str] = []
    async for event in router.stream(
        messages,
        [],
        model=settings.default_model,
        max_output_tokens=512,
    ):
        if event.kind == "text_delta":
            delta = event.payload.get("delta", "")
            if isinstance(delta, str):
                chunks.append(delta)

    return "".join(chunks)


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
