"""Proactive historical suggestion fan-out for guided workflow steps.

When a workflow step is entered, fields with ``suggestion.mode == "proactive"``
and ``"historical" in suggestion.sources`` should pre-fetch a recommended value
from PriceFRAME's ``/api/v1/agent/suggestions`` endpoint. The agent does this
in parallel (per-field) and emits ``v1.suggestion.ready`` (or
``v1.suggestion.no_signal``) events so the wizard can pre-fill the input.

Per-field isolation is mandatory: one failing call must not stop the rest.
This module returns *event dicts* — the caller is responsible for persisting
the events through :func:`append_run_event` so emission ordering stays in the
hands of the surrounding workflow code (which already manages the SSE stream).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from xframe_agent.agent.events import append_run_event
from xframe_agent.auth.jwt import AuthContext
from xframe_agent.priceframe import PriceFrameClient
from xframe_agent.tools.priceframe_read import (
    FieldSuggestionsInput,
    GetFieldSuggestionsTool,
)

logger = logging.getLogger(__name__)


EVENT_READY = "v1.suggestion.ready"
EVENT_NO_SIGNAL = "v1.suggestion.no_signal"


def _get(value: Any, key: str) -> Any:
    """Pull ``key`` off a Pydantic model or a plain mapping uniformly."""

    if value is None:
        return None
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)


def _enum_value(value: Any) -> Any:
    """Return the underlying value of a StrEnum (or pass through)."""

    if value is None:
        return None
    return getattr(value, "value", value)


def _coerce_sources(sources: Any) -> list[str]:
    if sources is None:
        return []
    if isinstance(sources, str):
        return [sources]
    if isinstance(sources, Iterable):
        return [_enum_value(item) for item in sources if item is not None]
    return []


def _coerce_filter_keys(keys: Any) -> list[str]:
    if keys is None:
        return []
    if isinstance(keys, str):
        return [keys]
    if isinstance(keys, Iterable):
        return [_enum_value(item) for item in keys if item is not None]
    return []


def build_suggestion_ctx(
    draft_state: Mapping[str, Any] | None,
    filter_keys: Iterable[str],
) -> dict[str, Any]:
    """Project draft state onto the contract-declared filter keys.

    The agent_suggestions endpoint expects a base64 JSON object with only the
    filter keys the field actually depends on. Each key is searched in the
    ``summary`` section of the draft (the workflow always stages PR-level
    facts there) and falls back to the top-level payload to cover any future
    contract layout that hoists keys directly.
    """

    if not draft_state:
        return {}

    summary_section = draft_state.get("summary") if isinstance(draft_state, Mapping) else None
    summary = summary_section if isinstance(summary_section, Mapping) else {}

    ctx: dict[str, Any] = {}
    for key in filter_keys:
        candidate = summary.get(key) if key in summary else draft_state.get(key)
        if candidate not in (None, "", []):
            ctx[key] = candidate
    return ctx


async def fan_out_historical_suggestions(
    contract: Any,
    step: Any,
    draft_state: Mapping[str, Any] | None,
    auth_ctx: AuthContext,
    priceframe: PriceFrameClient,
) -> list[dict[str, Any]]:
    """Pre-fetch proactive-historical suggestions for every applicable field.

    Returns an ordered list of event dicts in the shape::

        {"event_type": "v1.suggestion.ready", "payload": {...}}

    The caller decides emission order and persistence (so the function is safe
    to use from any workflow-entered code path). Failures are converted into
    ``v1.suggestion.no_signal`` events so the wizard always gets one event per
    proactive-historical field.
    """

    fields = list(_get(step, "fields") or [])
    step_id = _enum_value(_get(step, "id"))
    contract_id = _get(contract, "id") or _get(contract, "contract_id")
    contract_version = _get(contract, "version") or _get(contract, "contract_version")

    targets: list[tuple[str, dict[str, Any], Any]] = []
    for field in fields:
        suggestion = _get(field, "suggestion")
        if suggestion is None:
            continue
        mode = _enum_value(_get(suggestion, "mode"))
        if mode != "proactive":
            continue
        sources = _coerce_sources(_get(suggestion, "sources"))
        if "historical" not in sources:
            continue
        historical = _get(suggestion, "historical")
        filter_keys = _coerce_filter_keys(_get(historical, "filter_keys"))
        ctx = build_suggestion_ctx(draft_state, filter_keys)
        field_id = _get(field, "id")
        if not isinstance(field_id, str) or not field_id:
            continue
        targets.append((field_id, ctx, historical))

    if not targets:
        return []

    tool = GetFieldSuggestionsTool()

    async def _fetch_one(
        field_id: str,
        ctx: dict[str, Any],
        historical: Any,
    ) -> dict[str, Any]:
        base_payload: dict[str, Any] = {
            "field_id": field_id,
            "context_used": ctx,
        }
        if step_id is not None:
            base_payload["step_id"] = step_id
        if contract_id is not None:
            base_payload["contract_id"] = contract_id
        if contract_version is not None:
            base_payload["contract_version"] = contract_version
        try:
            args = FieldSuggestionsInput(field=field_id, ctx=ctx)
            output = await tool.execute(args, auth_ctx, priceframe)
        except Exception as exc:  # noqa: BLE001 - per-field isolation by design
            logger.warning(
                "historical suggestion fetch failed",
                extra={"field_id": field_id, "error": str(exc)},
            )
            payload = {**base_payload, "reason": "error", "error": str(exc)}
            return {"event_type": EVENT_NO_SIGNAL, "payload": payload}

        data = output.data if isinstance(output.data, Mapping) else None
        if data is None:
            payload = {**base_payload, "reason": "empty_response"}
            return {"event_type": EVENT_NO_SIGNAL, "payload": payload}
        if data.get("no_signal"):
            payload = {
                **base_payload,
                "reason": "below_min_sample_size",
                "sample_size": data.get("sample_size", 0),
                "basis": data.get("basis"),
                "as_of": data.get("as_of"),
            }
            return {"event_type": EVENT_NO_SIGNAL, "payload": payload}

        payload = {
            **base_payload,
            "value": data.get("value"),
            "unit": data.get("unit"),
            "sample_size": data.get("sample_size"),
            "range": data.get("range"),
            "basis": data.get("basis"),
            "as_of": data.get("as_of"),
        }
        if isinstance(data.get("context_used"), Mapping):
            payload["context_used"] = dict(data["context_used"])
        return {"event_type": EVENT_READY, "payload": payload}

    results = await asyncio.gather(
        *(_fetch_one(field_id, ctx, historical) for field_id, ctx, historical in targets),
        return_exceptions=False,
    )
    return list(results)


async def emit_historical_suggestions(
    session: AsyncSession,
    *,
    run_id: str,
    contract: Any,
    step: Any,
    draft_state: Mapping[str, Any] | None,
    auth_ctx: AuthContext,
    priceframe: PriceFrameClient | None,
) -> list[dict[str, Any]]:
    """Fan out proactive-historical suggestions and persist the resulting events.

    Returns the list of event dicts that were emitted (for tests + tracing).
    No-op when ``priceframe`` is ``None`` or the user lacks the read permission
    on the suggestions tool, so step entry never fails because of optional
    pre-fill metadata.
    """

    if priceframe is None:
        return []
    if not auth_ctx.has_permission("agent.suggestions.read"):
        return []

    events = await fan_out_historical_suggestions(
        contract=contract,
        step=step,
        draft_state=draft_state,
        auth_ctx=auth_ctx,
        priceframe=priceframe,
    )
    for event in events:
        await append_run_event(
            session,
            run_id=run_id,
            event_type=event["event_type"],
            payload=event["payload"],
        )
    return events


__all__ = [
    "EVENT_NO_SIGNAL",
    "EVENT_READY",
    "build_suggestion_ctx",
    "emit_historical_suggestions",
    "fan_out_historical_suggestions",
]
