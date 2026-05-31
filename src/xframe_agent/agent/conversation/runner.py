"""Glue: compute the next prompt, attach a suggestion for money fields, emit the event."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from xframe_agent.agent.conversation import events as ev
from xframe_agent.agent.conversation.recap import build_recap
from xframe_agent.agent.conversation.sequencer import FieldPrompt, Recap, next_step
from xframe_agent.agent.events import append_run_event


@dataclass(frozen=True)
class EmitResult:
    kind: str  # "prompt" | "recap" | "done"
    field_id: str | None = None


async def _suggestion_for(
    field: FieldPrompt,
    contract: dict[str, Any],
    draft_payload: dict[str, Any],
    auth_ctx: Any,
    priceframe: Any,
) -> dict[str, Any] | None:
    if not field.requires_explicit_confirm or priceframe is None or auth_ctx is None:
        return None
    try:
        from xframe_agent.agent.suggestions import fan_out_historical_suggestions

        step = next(
            (
                s
                for s in contract.get("steps", [])
                if any(f.get("id") == field.field_id for f in s.get("fields", []))
            ),
            None,
        )
        if step is None:
            return None
        single = {**step, "fields": [f for f in step["fields"] if f.get("id") == field.field_id]}
        results = await fan_out_historical_suggestions(
            contract=contract,
            step=single,
            draft_state=draft_payload,
            auth_ctx=auth_ctx,
            priceframe=priceframe,
        )
        for e in results:
            if e.get("event_type") == "v1.suggestion.ready":
                p = e["payload"]
                return {
                    "value": p.get("value"),
                    "basis": p.get("basis"),
                    "as_of": p.get("as_of"),
                    "sample_size": p.get("sample_size"),
                    "range": p.get("range"),
                }
    except Exception:  # noqa: BLE001 — suggestions are optional
        return None
    return None


async def emit_next_prompt(
    session: Any,
    *,
    run_id: str,
    contract: dict[str, Any],
    draft_payload: dict[str, Any],
    cursor: Any,
    auth_ctx: Any,
    priceframe: Any,
) -> EmitResult:
    step = next_step(contract, draft_payload, cursor)
    if isinstance(step, FieldPrompt):
        suggestion = await _suggestion_for(step, contract, draft_payload, auth_ctx, priceframe)
        await append_run_event(
            session,
            run_id=run_id,
            event_type=ev.EVENT_FIELD_PROMPT,
            payload=ev.field_prompt_payload(step, suggestion),
        )
        return EmitResult(kind="prompt", field_id=step.field_id)
    if isinstance(step, Recap):
        collected, defaulted = build_recap(contract, draft_payload)
        await append_run_event(
            session,
            run_id=run_id,
            event_type=ev.EVENT_CONVERSATION_RECAP,
            payload=ev.recap_payload(collected, defaulted),
        )
        return EmitResult(kind="recap")
    await append_run_event(
        session,
        run_id=run_id,
        event_type="v1.conversation.done",
        payload={},
    )
    return EmitResult(kind="done")
