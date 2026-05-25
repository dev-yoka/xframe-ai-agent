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
from xframe_agent.agent.suggestions_blend import (
    DEFAULT_HISTORICAL_MIN_SAMPLE,
    DEFAULT_MARKET_CONFIDENCE_THRESHOLD,
    BlendResult,
    FlashReconciler,
    blend_async,
)
from xframe_agent.agent.suggestions_budget import RunBudget
from xframe_agent.auth.jwt import AuthContext
from xframe_agent.observability.metrics import (
    increment_suggestion_no_signal,
    increment_suggestion_sources,
)
from xframe_agent.observability.tracing import suggestion_fanout_span
from xframe_agent.priceframe import PriceFrameClient
from xframe_agent.settings import Settings, get_settings
from xframe_agent.tools.priceframe_read import (
    FieldSuggestionsInput,
    GetFieldSuggestionsTool,
)
from xframe_agent.tools.web_research import (
    GeminiGroundingClient,
    ResearchCache,
    WebResearchInput,
    WebResearchTool,
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


def _historical_band_from_event(event: dict[str, Any] | None) -> dict[str, Any] | None:
    if event is None:
        return None
    if event.get("event_type") != EVENT_READY:
        return None
    payload = event.get("payload") or {}
    return {
        key: payload[key]
        for key in ("value", "unit", "sample_size", "range", "basis", "as_of", "context_used")
        if key in payload
    }


def _market_band_from_payload(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the market band sub-payload (or ``None`` when empty)."""

    if payload is None:
        return None
    value = payload.get("value")
    confidence = payload.get("confidence", 0.0)
    citations = payload.get("citations") or []
    if value is None and not citations and not confidence:
        return None
    return {
        "value": value,
        "confidence": confidence,
        "summary": payload.get("summary", ""),
        "citations": list(citations),
        "cache_hit": bool(payload.get("cache_hit")),
    }


def _render_research_query(
    template: str,
    draft_state: Mapping[str, Any] | None,
    field_id: str,
) -> str:
    """Best-effort ``str.format_map`` rendering of a market query template.

    Falls back to leaving placeholders in place when a key is missing — the
    Gemini call still issues a sensible query and the cache key is the same
    sha1, so missing context degrades to a slightly less specific question.
    """

    context: dict[str, Any] = {"field_id": field_id}
    if isinstance(draft_state, Mapping):
        summary = draft_state.get("summary") if isinstance(draft_state, Mapping) else None
        if isinstance(summary, Mapping):
            for key, value in summary.items():
                if isinstance(key, str):
                    context.setdefault(key, value)
        for key, value in draft_state.items():
            if isinstance(key, str):
                context.setdefault(key, value)

    class _SafeDict(dict[str, Any]):
        def __missing__(self, key: str) -> str:  # pragma: no cover - trivial
            return "{" + key + "}"

    try:
        return template.format_map(_SafeDict(context))
    except Exception:  # noqa: BLE001 - template is user-provided
        return template


async def fan_out_market_suggestions(
    contract: Any,
    step: Any,
    draft_state: Mapping[str, Any] | None,
    auth_ctx: AuthContext,
    *,
    settings: Settings,
    budget: RunBudget,
    cache: ResearchCache,
    client: GeminiGroundingClient | None,
) -> dict[str, dict[str, Any]]:
    """Run the market band for every proactive field with ``"market"`` in sources.

    Returns ``{field_id: payload}`` where payload is the WebResearchOutput dump
    (graceful empty for fields that timed out / hit budget / lacked an API
    key). Failures isolate per field — the dict still contains an entry for
    every market-eligible field so the blender can decide "no signal".
    """

    del contract  # contract identity is captured by the caller; not needed here

    fields = list(_get(step, "fields") or [])
    targets: list[tuple[str, str, int]] = []
    for field in fields:
        suggestion = _get(field, "suggestion")
        if suggestion is None:
            continue
        if _enum_value(_get(suggestion, "mode")) != "proactive":
            continue
        if "market" not in _coerce_sources(_get(suggestion, "sources")):
            continue
        market_spec = _get(suggestion, "market")
        if market_spec is None:
            continue
        template = _get(market_spec, "research_query_template")
        if not isinstance(template, str) or not template:
            continue
        max_age = _get(market_spec, "max_age_seconds")
        if not isinstance(max_age, int) or max_age <= 0:
            max_age = settings.web_research_default_max_age_seconds
        field_id = _get(field, "id")
        if not isinstance(field_id, str) or not field_id:
            continue
        targets.append((field_id, template, max_age))

    if not targets:
        return {}

    base_tool = WebResearchTool().with_runtime(
        settings=settings,
        cache=cache,
        client=client or GeminiGroundingClient(settings),
        budget=budget,
    )

    async def _research_one(field_id: str, template: str, max_age: int) -> dict[str, Any]:
        try:
            query = _render_research_query(template, draft_state, field_id)
            return await base_tool._research(
                WebResearchInput(query=query, max_age_seconds=max_age, context={})
            )
        except Exception as exc:  # noqa: BLE001 - per-field isolation
            logger.warning(
                "market suggestion fetch failed",
                extra={"field_id": field_id, "error": str(exc)},
            )
            return {
                "value": None,
                "confidence": 0.0,
                "summary": "research unavailable",
                "citations": [],
                "cache_hit": False,
            }

    pairs = list(targets)
    results = await asyncio.gather(
        *(_research_one(fid, tmpl, ma) for fid, tmpl, ma in pairs),
        return_exceptions=False,
    )
    return {field_id: payload for (field_id, _t, _m), payload in zip(pairs, results, strict=False)}


def _confidence_threshold_for(field: Any) -> float:
    suggestion = _get(field, "suggestion")
    threshold = _get(suggestion, "confidence_threshold") if suggestion is not None else None
    if isinstance(threshold, int | float):
        return float(threshold)
    return DEFAULT_MARKET_CONFIDENCE_THRESHOLD


def _historical_min_sample_for(field: Any) -> int:
    suggestion = _get(field, "suggestion")
    historical = _get(suggestion, "historical") if suggestion is not None else None
    min_sample = _get(historical, "min_sample_size") if historical is not None else None
    if isinstance(min_sample, int) and not isinstance(min_sample, bool):
        return min_sample
    return DEFAULT_HISTORICAL_MIN_SAMPLE


async def fan_out_suggestions(
    contract: Any,
    step: Any,
    draft_state: Mapping[str, Any] | None,
    auth_ctx: AuthContext,
    priceframe: PriceFrameClient,
    *,
    settings: Settings | None = None,
    budget: RunBudget | None = None,
    cache: ResearchCache | None = None,
    grounding_client: GeminiGroundingClient | None = None,
    flash_reconciler: FlashReconciler | None = None,
    parent_span: Any | None = None,
) -> list[dict[str, Any]]:
    """Fan out historical *and* market bands in parallel then blend per field.

    Returns ordered event dicts. ``v1.suggestion.ready`` events always carry
    both bands (whichever was unavailable is ``null``); ``v1.suggestion.no_signal``
    is emitted only when both bands fail the blend's confidence floor.
    """

    if settings is None:
        settings = get_settings()
    if budget is None:
        budget = RunBudget(
            max_calls=settings.max_research_calls_per_run,
            max_cost_usd=settings.max_research_cost_per_run_usd,
        )
    if cache is None:
        cache = ResearchCache()
    if grounding_client is None:
        grounding_client = GeminiGroundingClient(settings)
    if flash_reconciler is None and grounding_client.configured:
        flash_reconciler = FlashReconciler(settings, budget=budget)

    step_id_for_span = _enum_value(_get(step, "id")) or "unknown"
    field_count = len(list(_get(step, "fields") or []))
    # Tracing is best-effort; the span context manager is a no-op when
    # Langfuse isn't configured so we always enter it. ``parent_span`` is
    # provided by the workflow step span when wired through ``emit_suggestions``;
    # when ``None`` the fan-out still gets its own span but is detached from
    # the per-step hierarchy.
    with suggestion_fanout_span(
        parent_span,
        step_id=str(step_id_for_span),
        field_count=field_count,
    ):
        historical_task = asyncio.create_task(
            fan_out_historical_suggestions(
                contract=contract,
                step=step,
                draft_state=draft_state,
                auth_ctx=auth_ctx,
                priceframe=priceframe,
            )
        )
        market_task = asyncio.create_task(
            fan_out_market_suggestions(
                contract=contract,
                step=step,
                draft_state=draft_state,
                auth_ctx=auth_ctx,
                settings=settings,
                budget=budget,
                cache=cache,
                client=grounding_client,
            )
        )
        historical_events, market_payloads = await asyncio.gather(
            historical_task, market_task
        )

    fields_by_id: dict[str, Any] = {}
    for field in _get(step, "fields") or []:
        field_id = _get(field, "id")
        if isinstance(field_id, str):
            fields_by_id[field_id] = field

    # Index historical events by field id so we can pair with market payloads.
    historical_by_field: dict[str, dict[str, Any]] = {}
    for event in historical_events:
        payload = event.get("payload") or {}
        field_id = payload.get("field_id")
        if isinstance(field_id, str):
            historical_by_field[field_id] = event

    contract_id = _get(contract, "id") or _get(contract, "contract_id")
    contract_version = _get(contract, "version") or _get(contract, "contract_version")
    step_id = _enum_value(_get(step, "id"))

    eligible_field_ids: list[str] = []
    for field in _get(step, "fields") or []:
        suggestion = _get(field, "suggestion")
        if suggestion is None:
            continue
        if _enum_value(_get(suggestion, "mode")) != "proactive":
            continue
        sources = _coerce_sources(_get(suggestion, "sources"))
        if "historical" not in sources and "market" not in sources:
            continue
        field_id = _get(field, "id")
        if isinstance(field_id, str) and field_id:
            eligible_field_ids.append(field_id)

    blended_events: list[dict[str, Any]] = []
    for field_id in eligible_field_ids:
        field = fields_by_id.get(field_id)
        historical_event = historical_by_field.get(field_id)
        historical_band = _historical_band_from_event(historical_event)
        market_payload = market_payloads.get(field_id)
        market_band = _market_band_from_payload(market_payload)

        base_payload: dict[str, Any] = {"field_id": field_id}
        if step_id is not None:
            base_payload["step_id"] = step_id
        if contract_id is not None:
            base_payload["contract_id"] = contract_id
        if contract_version is not None:
            base_payload["contract_version"] = contract_version
        if historical_event is not None:
            inherited = historical_event.get("payload") or {}
            for key in ("context_used", "as_of"):
                if key in inherited and key not in base_payload:
                    base_payload[key] = inherited[key]

        # If the field has no market source declared, preserve legacy
        # behaviour: emit the historical event unchanged (caller still gets
        # ``v1.suggestion.ready`` with the same payload shape Wave B added).
        suggestion_sources = _coerce_sources(
            _get(_get(field, "suggestion"), "sources") if field is not None else None
        )
        if "market" not in suggestion_sources and historical_event is not None:
            # Historical-only fan-outs still feed source/no_signal metrics so
            # the dashboard funnel is complete even when market is disabled.
            if historical_event.get("event_type") == EVENT_READY:
                increment_suggestion_sources(["historical"])
            elif historical_event.get("event_type") == EVENT_NO_SIGNAL:
                increment_suggestion_no_signal(field_id)
            blended_events.append(historical_event)
            continue

        # Need to run the blender — either market is required, or market
        # came back unavailable but we still want a blended payload shape.
        confidence_threshold = _confidence_threshold_for(field)
        min_sample = _historical_min_sample_for(field)
        result: BlendResult = await blend_async(
            historical=historical_band,
            market=market_band,
            min_sample_size=min_sample,
            confidence_threshold=confidence_threshold,
            disagreement_threshold=settings.web_research_disagreement_threshold,
            flash_reconciler=flash_reconciler,
        )

        if result.no_signal:
            increment_suggestion_no_signal(field_id)
            payload = {
                **base_payload,
                "reason": result.reason or "confidence_below_threshold",
                "historical": historical_band,
                "market": market_band,
            }
            blended_events.append({"event_type": EVENT_NO_SIGNAL, "payload": payload})
            continue

        # Telemetry: count each source the blend actually consumed. When the
        # blend pulled from both bands we *also* emit a synthetic ``blended``
        # source so the dashboard panel can distinguish a pure single-source
        # suggestion from a true blend.
        sources_for_metric = list(result.sources_used)
        if len(set(sources_for_metric) & {"historical", "market"}) == 2:
            sources_for_metric.append("blended")
        increment_suggestion_sources(sources_for_metric)

        payload = {
            **base_payload,
            "historical": historical_band,
            "market": market_band,
            "proposed": {
                "value": result.value,
                "rationale": result.rationale,
                "sources_used": result.sources_used,
            },
        }
        # For backwards-compat with Wave B consumers that read top-level value,
        # echo the proposed value/range/etc. when they came from historical.
        if historical_band is not None and "historical" in result.sources_used:
            for key in ("unit", "sample_size", "range", "basis"):
                if key in historical_band and key not in payload:
                    payload[key] = historical_band[key]
        if "value" not in payload and result.value is not None:
            payload["value"] = result.value
        blended_events.append({"event_type": EVENT_READY, "payload": payload})

    return blended_events


async def emit_suggestions(
    session: AsyncSession,
    *,
    run_id: str,
    contract: Any,
    step: Any,
    draft_state: Mapping[str, Any] | None,
    auth_ctx: AuthContext,
    priceframe: PriceFrameClient | None,
    settings: Settings | None = None,
    budget: RunBudget | None = None,
    cache: ResearchCache | None = None,
    grounding_client: GeminiGroundingClient | None = None,
    flash_reconciler: FlashReconciler | None = None,
    parent_span: Any | None = None,
) -> list[dict[str, Any]]:
    """Combined historical + market emit. Use this instead of the historical-only one.

    ``parent_span`` is forwarded into :func:`fan_out_suggestions` so the
    Langfuse span hierarchy nests under the active ``agent.workflow_step``
    span when supplied (Phase 11 / M2-GA open item).
    """

    if priceframe is None:
        return []
    if not auth_ctx.has_permission("agent.suggestions.read"):
        return []

    events = await fan_out_suggestions(
        contract=contract,
        step=step,
        draft_state=draft_state,
        auth_ctx=auth_ctx,
        priceframe=priceframe,
        settings=settings,
        budget=budget,
        cache=cache,
        grounding_client=grounding_client,
        flash_reconciler=flash_reconciler,
        parent_span=parent_span,
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
    "emit_suggestions",
    "fan_out_historical_suggestions",
    "fan_out_market_suggestions",
    "fan_out_suggestions",
]
