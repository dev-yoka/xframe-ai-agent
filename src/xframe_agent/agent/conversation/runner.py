"""Glue: compute the next prompt, attach a suggestion for money fields, emit the event."""
from __future__ import annotations

import dataclasses
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


async def _resolve_options(
    prompt: FieldPrompt,
    draft_payload: dict[str, Any],
    auth_ctx: Any,
    priceframe: Any,
) -> list[dict[str, str]] | None:
    """Best-effort: fetch options for API-sourced fields. Never raises."""
    if not prompt.options_source or priceframe is None or auth_ctx is None:
        return None
    if prompt.options:  # already have static options
        return None
    try:
        options_source = prompt.options_source
        endpoint = options_source.get("endpoint", "")
        value_path = options_source.get("value_path", "")
        depends_on = options_source.get("depends_on") or []

        # Fetch from PriceFRAME using the user's JWT
        data = await priceframe.get_json(endpoint, jwt_raw=auth_ctx.jwt_raw)
        if not isinstance(data, dict):
            return None

        # Extract the raw list at value_path.
        raw = data.get(value_path) or []
        if not raw and value_path == "countries":
            # Flatten countriesByRegion as a fallback when top-level "countries" key absent.
            by_region_flat: dict[str, list[str]] = data.get("countriesByRegion") or {}
            seen: set[str] = set()
            flat: list[str] = []
            for country_list in by_region_flat.values():
                for c in country_list:
                    if c not in seen:
                        flat.append(c)
                        seen.add(c)
            raw = flat

        # Filter countries by a previously-selected region if the field declares
        # depends_on.  Two cases: corridor_countries filtered by corridor_regions,
        # and sending_partner_country filtered by sending_partner_region.
        region_dep = next(
            (dep for dep in depends_on if dep in ("corridor_regions", "sending_partner_region")),
            None,
        )
        if region_dep is not None:
            selected_regions: list[str] = []
            summary = draft_payload.get("summary") if isinstance(draft_payload, dict) else {}
            if isinstance(summary, dict):
                selected_regions = summary.get(region_dep) or []
                # sending_partner_region is a scalar string, not a list
                if isinstance(selected_regions, str):
                    selected_regions = [selected_regions]
            if selected_regions:
                by_region: dict[str, list[str]] = data.get("countriesByRegion") or {}
                filtered: list[str] = []
                for region in selected_regions:
                    filtered.extend(by_region.get(region) or [])
                if filtered:
                    raw = list(dict.fromkeys(filtered))  # dedupe preserving order

        if not raw:
            return None

        # Normalize to [{value, label}]
        if isinstance(raw[0], str):
            return [{"value": item, "label": item} for item in raw]
        if isinstance(raw[0], dict):
            return [
                {
                    "value": item.get("value", item.get("id", str(item))),
                    "label": item.get("label", item.get("name", str(item))),
                }
                for item in raw
            ]
        return None
    except Exception:  # noqa: BLE001 — options resolution is best-effort, never blocks
        return None


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
        resolved_options = await _resolve_options(step, draft_payload, auth_ctx, priceframe)
        if resolved_options:
            step = dataclasses.replace(step, options=resolved_options)
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
