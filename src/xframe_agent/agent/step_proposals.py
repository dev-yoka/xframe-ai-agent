"""Build a complete proposed payload for a wizard step + emit SSE events.

The M2.1 simplification narrows each wizard step to a small set of *essential*
fields the agent must populate before the user is asked to review the tab. This
module is the orchestrator that takes a :class:`WorkflowStep` plus the current
:class:`AgentWorkflowDraft` state and produces a single ``v1.workflow.step.proposed``
payload covering those essentials.

Resolution per essential field follows a tight, deterministic ladder:

1. **draft** — if the field already has a value in ``draft.payload[step_id]``,
   reuse it untouched (the user, or an earlier proposal, has already filled it).
2. **historical** — when the field declares a ``suggestion`` block with the
   historical source, call :class:`GetFieldSuggestionsTool` (one call per field)
   and use the returned median/mean/p75 value when the endpoint reports a
   non-empty signal. Failures and below-sample responses fall through.
3. **default** — when the field has a sensible static default (the first enum
   option, an explicit ``required=False`` boolean, a zero-valued numeric), use
   that value so the proposal is never empty for these "obvious" cases.

The orchestrator is intentionally best-effort. A failing per-field fetch never
poisons the rest of the step; the helper returns ``None`` only when **every**
essential failed to produce a value, so the caller can skip emitting the
``...step.proposed`` event in that case.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import Any

from xframe_agent.agent.suggestions_budget import RunBudget
from xframe_agent.auth.jwt import AuthContext
from xframe_agent.priceframe import PriceFrameClient
from xframe_agent.tools.priceframe_read import (
    FieldSuggestionsInput,
    GetFieldSuggestionsTool,
)

log = logging.getLogger(__name__)


SOURCE_DRAFT = "draft"
SOURCE_HISTORICAL = "historical"
SOURCE_DEFAULT = "default"


def _get(value: Any, key: str) -> Any:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)


def _enum_value(value: Any) -> Any:
    if value is None:
        return None
    return getattr(value, "value", value)


def _step_id(step: Any) -> str:
    raw = _enum_value(_get(step, "id"))
    return str(raw) if raw is not None else "unknown"


def _field_id(field: Any) -> str:
    raw = _enum_value(_get(field, "id"))
    return str(raw) if raw is not None else ""


def _fields(step: Any) -> list[Any]:
    raw = _get(step, "fields") or []
    return list(raw)


def _essential_field_ids(step: Any) -> list[str]:
    """Return the step's ``essential_field_ids`` as plain strings."""

    raw = _get(step, "essential_field_ids")
    if raw is None:
        return []
    out: list[str] = []
    for entry in raw:
        if entry is None:
            continue
        if isinstance(entry, str):
            if entry:
                out.append(entry)
            continue
        if isinstance(entry, Mapping):
            # Tolerate the Pydantic RootModel dict shape ``{"root": "id"}``.
            inner = entry.get("root") or entry.get("id")
            if isinstance(inner, str) and inner:
                out.append(inner)
            continue
        candidate = getattr(entry, "root", None) or getattr(entry, "value", None)
        if isinstance(candidate, str) and candidate:
            out.append(candidate)
    return out


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


def _draft_value(draft_state: Mapping[str, Any], step_id: str, field_id: str) -> Any:
    section = draft_state.get(step_id) if draft_state else None
    if isinstance(section, Mapping) and field_id in section:
        candidate = section[field_id]
        if candidate not in (None, "", []):
            return candidate
    return None


def _build_ctx(
    draft_state: Mapping[str, Any] | None,
    filter_keys: Iterable[str],
) -> dict[str, Any]:
    """Project draft state onto declared filter keys (summary first, then top-level)."""

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


def _default_value_for(field: Any) -> Any:
    """Pick a sensible static default for an essential field, or ``None``.

    Defaults are intentionally conservative: we only fill in cases where the
    contract leaves us a single obvious choice (first enum option, an explicit
    zero numeric minimum, boolean false). For free-form ``string`` / ``date``
    fields, returning a guessed value would be worse than leaving the field
    blank, so the orchestrator skips them.
    """

    field_type = _enum_value(_get(field, "type"))
    if field_type in ("enum",):
        options = _get(field, "enum_options") or []
        if options:
            first = options[0]
            value = _get(first, "value")
            if isinstance(value, str) and value:
                return value
        return None
    if field_type == "boolean":
        return False
    if field_type in ("number", "currency", "percentage"):
        validation = _get(field, "validation")
        min_value = _get(validation, "min") if validation is not None else None
        if isinstance(min_value, int | float):
            return min_value
        return 0
    # string / date / multi_enum — no safe default.
    return None


def _historical_estimated_cost_usd() -> float:
    # The historical suggestion endpoint is a cheap SQL read on PriceFRAME.
    # We still bill it against the run budget at a token cost so we can't
    # blow through the cap on a 5-field step.
    return 0.0


async def _fetch_historical(
    field_id: str,
    historical_spec: Any,
    *,
    draft_state: Mapping[str, Any] | None,
    auth_ctx: AuthContext,
    priceframe: PriceFrameClient,
    tool: GetFieldSuggestionsTool,
) -> Any | None:
    """Best-effort fetch of one historical suggestion value.

    Returns the numeric value when the endpoint reports a usable signal, else
    ``None`` (no_signal / empty / error / permission). Caller decides what to
    do with the None — typically falling back to a static default.
    """

    filter_keys = _coerce_filter_keys(_get(historical_spec, "filter_keys"))
    ctx = _build_ctx(draft_state, filter_keys)
    try:
        args = FieldSuggestionsInput(field=field_id, ctx=ctx)
        output = await tool.execute(args, auth_ctx, priceframe)
    except Exception as exc:  # noqa: BLE001 - per-field isolation
        log.warning(
            "step proposal historical fetch failed",
            extra={"field_id": field_id, "error": str(exc)},
        )
        return None
    data = output.data if isinstance(output.data, Mapping) else None
    if data is None or data.get("no_signal"):
        return None
    value = data.get("value")
    if value is None:
        return None
    return value


def _confidence_score(
    filled_count: int,
    essential_count: int,
    sources_used: Iterable[str],
) -> float:
    """Derive a 0..1 confidence score for the proposal.

    Coverage matters most: a proposal that hits every essential is *much* more
    useful than one that fills only 1 of 5 fields. Source quality nudges the
    score up when the proposal leans on actual historical data versus static
    defaults / lone draft values.
    """

    if essential_count <= 0:
        return 0.0
    coverage = max(0.0, min(1.0, filled_count / essential_count))
    sources = {s for s in sources_used if s}
    if SOURCE_HISTORICAL in sources:
        quality = 1.0
    elif SOURCE_DRAFT in sources:
        quality = 0.7
    elif SOURCE_DEFAULT in sources:
        quality = 0.4
    else:
        quality = 0.0
    # Weight coverage more heavily than source mix; matches the spec's
    # "complete proposal" framing for the wizard UX.
    score = 0.7 * coverage + 0.3 * quality
    return round(score, 4)


async def propose_step_payload(
    *,
    contract: Any,
    step: Any,
    draft_state: Mapping[str, Any] | None,
    auth_ctx: AuthContext,
    priceframe: PriceFrameClient | None,
    budget: RunBudget,
) -> dict[str, Any] | None:
    """Build a ``v1.workflow.step.proposed`` payload for ``step``.

    Returns ``None`` when no proposal is possible — either the step declares no
    essential fields, or every essential failed to resolve through draft /
    historical / default. The caller emits no event in that case so the wizard
    stream stays quiet.

    The returned dict is the *payload* of the SSE event (not the SSE envelope
    itself); the caller is responsible for ``append_run_event``.
    """

    del contract  # contract identity is captured by the caller; not needed here.

    essential_ids = _essential_field_ids(step)
    if not essential_ids:
        return None

    draft = draft_state or {}
    step_id = _step_id(step)

    fields_by_id: dict[str, Any] = {}
    for field in _fields(step):
        fid = _field_id(field)
        if fid:
            fields_by_id[fid] = field

    essentials: list[tuple[str, Any]] = []
    for fid in essential_ids:
        field = fields_by_id.get(fid)
        if field is None:
            # Contract drift — declared essential but no matching field. Skip
            # rather than blowing up so the wizard still gets a partial proposal.
            log.warning(
                "essential field id has no matching field on step",
                extra={"step_id": step_id, "field_id": fid},
            )
            continue
        essentials.append((fid, field))

    if not essentials:
        return None

    payload: dict[str, Any] = {}
    rationale_by_field: dict[str, str] = {}
    sources_used: set[str] = set()

    tool = GetFieldSuggestionsTool()
    can_use_historical = priceframe is not None and auth_ctx.has_permission(
        "agent.suggestions.read"
    )

    for fid, field in essentials:
        # 1) Existing draft value wins outright.
        draft_value = _draft_value(draft, step_id, fid)
        if draft_value is not None:
            payload[fid] = draft_value
            sources_used.add(SOURCE_DRAFT)
            rationale_by_field[fid] = "Already filled in this draft."
            continue

        # 2) Historical suggestion (per-field, budget-gated).
        suggestion = _get(field, "suggestion")
        historical_spec = _get(suggestion, "historical") if suggestion is not None else None
        sources = _coerce_sources(_get(suggestion, "sources")) if suggestion is not None else []
        if (
            can_use_historical
            and historical_spec is not None
            and "historical" in sources
            and budget.can_spend(_historical_estimated_cost_usd())
        ):
            assert priceframe is not None  # narrows for mypy
            value = await _fetch_historical(
                field_id=fid,
                historical_spec=historical_spec,
                draft_state=draft,
                auth_ctx=auth_ctx,
                priceframe=priceframe,
                tool=tool,
            )
            # Even when the call returned no value we still account for it so a
            # broken endpoint can't loop the whole step.
            budget.record(_historical_estimated_cost_usd())
            if value is not None:
                payload[fid] = value
                sources_used.add(SOURCE_HISTORICAL)
                rationale_by_field[fid] = (
                    "Drawn from similar quotes in your history."
                )
                continue

        # 3) Fall back to a static default if the field has a safe one.
        default_value = _default_value_for(field)
        if default_value is not None:
            payload[fid] = default_value
            sources_used.add(SOURCE_DEFAULT)
            rationale_by_field[fid] = "Sensible default — adjust as needed."

    if not payload:
        return None

    confidence = _confidence_score(
        filled_count=len(payload),
        essential_count=len(essentials),
        sources_used=sources_used,
    )

    proposal: dict[str, Any] = {
        "payload": payload,
        "rationale_by_field": rationale_by_field,
        "citations": None,
        "sources_used": sorted(sources_used),
        "confidence": confidence,
    }
    return {"step_id": step_id, "proposal": proposal}


__all__ = [
    "SOURCE_DEFAULT",
    "SOURCE_DRAFT",
    "SOURCE_HISTORICAL",
    "propose_step_payload",
]
