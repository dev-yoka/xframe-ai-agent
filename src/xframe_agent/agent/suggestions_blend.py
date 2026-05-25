"""Deterministic blending of historical + market suggestions (M2 Phase 9).

Implements the rules in spec §5.3:

* ``historical.sample_size ≥ 10`` AND ``market.confidence ≥ 0.7`` → 70/30 blend
  (historical-weighted).
* ``3 ≤ historical.sample_size < 10`` AND market available → 50/50 blend.
* ``historical.sample_size < 3`` AND ``market.confidence ≥ 0.5`` → market only
  (rationale "low-data corridor — market only").
* Both bands weak → no_signal.
* Disagreement > 25% → reconcile via Gemini Flash structured output (with a
  deterministic 50/50 fallback when budget/key are unavailable).

The output of :func:`blend` is the *proposed* sub-payload that the SSE
``v1.suggestion.ready`` event embeds alongside the raw historical and market
bands. ``no_signal=True`` instead means the caller should emit
``v1.suggestion.no_signal`` instead.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from xframe_agent.agent.suggestions_budget import RunBudget
from xframe_agent.settings import Settings

logger = logging.getLogger(__name__)


HISTORICAL_HIGH_SAMPLE = 10
HISTORICAL_LOW_SAMPLE = 3
MARKET_STRONG_CONFIDENCE = 0.7
MARKET_MODERATE_CONFIDENCE = 0.5
DEFAULT_HISTORICAL_MIN_SAMPLE = 3
DEFAULT_MARKET_CONFIDENCE_THRESHOLD = 0.5


@dataclass(slots=True)
class BlendResult:
    """Outcome of :func:`blend`."""

    no_signal: bool
    value: float | None
    rationale: str
    sources_used: list[str]
    reason: str | None = None  # populated when no_signal=True

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "value": self.value,
            "rationale": self.rationale,
            "sources_used": list(self.sources_used),
        }
        return payload


def _historical_value(historical: Mapping[str, Any] | None) -> float | None:
    if not historical:
        return None
    value = historical.get("value")
    if isinstance(value, int | float):
        return float(value)
    return None


def _historical_sample_size(historical: Mapping[str, Any] | None) -> int:
    if not historical:
        return 0
    sample = historical.get("sample_size")
    if isinstance(sample, int) and not isinstance(sample, bool):
        return sample
    return 0


def _market_value(market: Mapping[str, Any] | None) -> float | None:
    if not market:
        return None
    value = market.get("value")
    if isinstance(value, int | float):
        return float(value)
    return None


def _market_confidence(market: Mapping[str, Any] | None) -> float:
    if not market:
        return 0.0
    confidence = market.get("confidence")
    if isinstance(confidence, int | float):
        try:
            return float(confidence)
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def _disagreement(historical_value: float | None, market_value: float | None) -> float:
    """Relative disagreement between two values (0 when either is missing)."""

    if historical_value is None or market_value is None:
        return 0.0
    if historical_value == 0:
        return float("inf") if market_value != 0 else 0.0
    return abs(historical_value - market_value) / abs(historical_value)


def blend(
    historical: Mapping[str, Any] | None,
    market: Mapping[str, Any] | None,
    *,
    min_sample_size: int = DEFAULT_HISTORICAL_MIN_SAMPLE,
    confidence_threshold: float = DEFAULT_MARKET_CONFIDENCE_THRESHOLD,
    disagreement_threshold: float = 0.25,
    flash_reconciler: FlashReconciler | None = None,
) -> BlendResult:
    """Apply the spec §5.3 deterministic ladder.

    ``flash_reconciler`` is only consulted when both bands have values and
    disagree by more than ``disagreement_threshold``. When it's ``None`` (or
    raises), the disagreement falls back to a 50/50 blend so the wizard keeps
    moving.
    """

    historical_size = _historical_sample_size(historical)
    historical_value = _historical_value(historical)
    market_value = _market_value(market)
    market_confidence = _market_confidence(market)

    historical_strong = historical_size >= HISTORICAL_HIGH_SAMPLE and historical_value is not None
    historical_moderate = (
        HISTORICAL_LOW_SAMPLE <= historical_size < HISTORICAL_HIGH_SAMPLE
        and historical_value is not None
    )
    historical_weak = historical_size < min_sample_size or historical_value is None
    market_strong = market_value is not None and market_confidence >= MARKET_STRONG_CONFIDENCE
    market_present = market_value is not None and market_confidence > 0.0

    # Both bands weak → no signal.
    if historical_weak and market_confidence < confidence_threshold:
        return BlendResult(
            no_signal=True,
            value=None,
            rationale="",
            sources_used=[],
            reason="confidence_below_threshold",
        )

    # Strong + strong → 70/30 weighted blend (historical-weighted).
    if historical_strong and market_strong:
        return _maybe_reconcile(
            historical_value=historical_value,
            market_value=market_value,
            default_value=_weighted(historical_value, market_value, 0.7, 0.3),
            default_rationale="strong historical + strong market signal (70/30)",
            sources_used=["historical", "market"],
            disagreement_threshold=disagreement_threshold,
            flash_reconciler=flash_reconciler,
        )

    # Moderate historical + market available → 50/50.
    if historical_moderate and market_present:
        return _maybe_reconcile(
            historical_value=historical_value,
            market_value=market_value,
            default_value=_weighted(historical_value, market_value, 0.5, 0.5),
            default_rationale="moderate historical + market (50/50)",
            sources_used=["historical", "market"],
            disagreement_threshold=disagreement_threshold,
            flash_reconciler=flash_reconciler,
        )

    # Low historical, market strong enough → market only.
    if (
        historical_size < HISTORICAL_LOW_SAMPLE
        and market_value is not None
        and market_confidence >= MARKET_MODERATE_CONFIDENCE
    ):
        return BlendResult(
            no_signal=False,
            value=market_value,
            rationale="low-data corridor — market only",
            sources_used=["market"],
        )

    # Historical only — moderate/strong historical without a usable market band.
    if historical_value is not None and historical_size >= min_sample_size:
        return BlendResult(
            no_signal=False,
            value=historical_value,
            rationale="historical only",
            sources_used=["historical"],
        )

    # Market only — present but historical missing entirely.
    if market_value is not None and market_confidence >= confidence_threshold:
        return BlendResult(
            no_signal=False,
            value=market_value,
            rationale="market only",
            sources_used=["market"],
        )

    return BlendResult(
        no_signal=True,
        value=None,
        rationale="",
        sources_used=[],
        reason="confidence_below_threshold",
    )


def _weighted(a: float | None, b: float | None, wa: float, wb: float) -> float | None:
    if a is None or b is None:
        return a if a is not None else b
    total_weight = wa + wb
    if total_weight == 0:
        return None
    return (a * wa + b * wb) / total_weight


def _maybe_reconcile(
    *,
    historical_value: float | None,
    market_value: float | None,
    default_value: float | None,
    default_rationale: str,
    sources_used: list[str],
    disagreement_threshold: float,
    flash_reconciler: FlashReconciler | None,
) -> BlendResult:
    if (
        historical_value is not None
        and market_value is not None
        and _disagreement(historical_value, market_value) > disagreement_threshold
    ):
        if flash_reconciler is not None:
            try:
                reconciled = asyncio_run_if_needed(
                    flash_reconciler.reconcile(historical=historical_value, market=market_value)
                )
            except RuntimeError:
                reconciled = None
            except Exception as exc:  # noqa: BLE001 - graceful fallback
                logger.warning("flash reconcile failed", extra={"error": str(exc)})
                reconciled = None
            if reconciled is not None and reconciled.get("recommended") is not None:
                return BlendResult(
                    no_signal=False,
                    value=float(reconciled["recommended"]),
                    rationale=str(
                        reconciled.get("rationale")
                        or "disagreement >25% — reconciled by Gemini Flash"
                    ),
                    sources_used=list(sources_used) + ["flash"],
                )
        return BlendResult(
            no_signal=False,
            value=default_value,
            rationale=f"{default_rationale} (disagreement >25% — Flash fallback unavailable)",
            sources_used=sources_used,
        )
    return BlendResult(
        no_signal=False,
        value=default_value,
        rationale=default_rationale,
        sources_used=sources_used,
    )


def asyncio_run_if_needed(awaitable: Any) -> Any:
    """Run an awaitable to completion in any context.

    ``blend`` is synchronous so callers can use it from non-async code paths
    (e.g. tests). When invoked inside an event loop, this raises ``RuntimeError``
    and the caller swallows it; the suggestion service exposes an async
    :func:`blend_async` wrapper for use inside the fan-out.
    """

    if asyncio.iscoroutine(awaitable):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(awaitable)
        # Running inside an event loop — we can't block. Caller should use
        # ``blend_async`` instead.
        raise RuntimeError("blend called inside a running loop; use blend_async instead")
    return awaitable


async def blend_async(
    historical: Mapping[str, Any] | None,
    market: Mapping[str, Any] | None,
    *,
    min_sample_size: int = DEFAULT_HISTORICAL_MIN_SAMPLE,
    confidence_threshold: float = DEFAULT_MARKET_CONFIDENCE_THRESHOLD,
    disagreement_threshold: float = 0.25,
    flash_reconciler: FlashReconciler | None = None,
) -> BlendResult:
    """Async wrapper around :func:`blend` for the fan-out call path."""

    historical_size = _historical_sample_size(historical)
    historical_value = _historical_value(historical)
    market_value = _market_value(market)
    market_confidence = _market_confidence(market)

    needs_reconcile = (
        historical_value is not None
        and market_value is not None
        and _disagreement(historical_value, market_value) > disagreement_threshold
        and (
            (
                historical_size >= HISTORICAL_HIGH_SAMPLE
                and market_confidence >= MARKET_STRONG_CONFIDENCE
            )
            or (
                HISTORICAL_LOW_SAMPLE <= historical_size < HISTORICAL_HIGH_SAMPLE
                and market_confidence > 0.0
            )
        )
    )

    if (
        needs_reconcile
        and flash_reconciler is not None
        and historical_value is not None
        and market_value is not None
    ):
        try:
            reconciled = await flash_reconciler.reconcile(
                historical=historical_value, market=market_value
            )
        except Exception as exc:  # noqa: BLE001 - graceful fallback
            logger.warning("flash reconcile failed", extra={"error": str(exc)})
            reconciled = None
        if reconciled is not None and reconciled.get("recommended") is not None:
            sources_used = ["historical", "market", "flash"]
            return BlendResult(
                no_signal=False,
                value=float(reconciled["recommended"]),
                rationale=str(
                    reconciled.get("rationale") or "disagreement >25% — reconciled by Gemini Flash"
                ),
                sources_used=sources_used,
            )

    # No reconciliation needed (or reconciler unavailable) — delegate to the
    # deterministic implementation with ``flash_reconciler=None``.
    return blend(
        historical=historical,
        market=market,
        min_sample_size=min_sample_size,
        confidence_threshold=confidence_threshold,
        disagreement_threshold=disagreement_threshold,
        flash_reconciler=None,
    )


class FlashReconciler:
    """Tiny Gemini-Flash structured-output wrapper used for disagreement repair.

    Kept narrow on purpose: the deterministic blender owns the rules, this
    class only resolves the ``>25%`` disagreement case by asking Flash to
    pick one number with a one-line rationale. Returns ``None`` when budget
    is exhausted, the API key is missing, or the call fails — the blender
    then degrades to a 50/50 fallback.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        budget: RunBudget,
        client: httpx.AsyncClient | None = None,
        model: str | None = None,
        estimated_cost_usd: float | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self._settings = settings
        self._budget = budget
        self._client = client
        self._model = model or settings.web_research_fallback_model
        self._api_key = settings.gemini_developer_api_key
        self._base_url = settings.gemini_api_base_url.rstrip("/")
        self._estimated_cost_usd = (
            estimated_cost_usd
            if estimated_cost_usd is not None
            else settings.web_research_estimated_cost_usd
        )
        self._timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else settings.web_research_timeout_seconds
        )

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    async def reconcile(
        self,
        *,
        historical: float,
        market: float,
    ) -> dict[str, Any] | None:
        if not self.configured:
            return None
        if not self._budget.can_spend(self._estimated_cost_usd):
            logger.info(
                "flash reconcile skipped — budget exhausted",
                extra={"budget": self._budget.snapshot()},
            )
            return None
        payload = _flash_request_payload(historical=historical, market=market)
        url = f"{self._base_url}/models/{self._model}:generateContent"
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self._api_key or "",
        }
        try:
            async with asyncio.timeout(self._timeout_seconds):
                if self._client is not None:
                    response = await self._client.post(url, headers=headers, json=payload)
                else:
                    async with httpx.AsyncClient(
                        timeout=httpx.Timeout(self._timeout_seconds)
                    ) as client:
                        response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
        except TimeoutError:
            logger.info("flash reconcile timed out")
            return None
        except Exception as exc:  # noqa: BLE001
            logger.warning("flash reconcile error", extra={"error": str(exc)})
            return None
        self._budget.record(self._estimated_cost_usd)
        return _parse_flash_response(response.json())


def _flash_request_payload(*, historical: float, market: float) -> dict[str, Any]:
    instruction = (
        "Two estimates disagree by more than 25%. Pick one recommended value "
        "(or a justified blend) and explain in one sentence which signal you "
        "weighted more.\n"
        f"historical = {historical}\n"
        f"market = {market}\n"
        "Respond with a single JSON object on the last line containing the "
        'keys "recommended" (a number) and "rationale" (a string).'
    )
    return {
        "contents": [{"role": "user", "parts": [{"text": instruction}]}],
        "generationConfig": {
            "temperature": 0.0,
            "maxOutputTokens": 128,
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "object",
                "properties": {
                    "recommended": {"type": "number"},
                    "rationale": {"type": "string"},
                },
                "required": ["recommended", "rationale"],
            },
        },
    }


_FLASH_JSON_RE = re.compile(r"\{[^{}]*\}", re.DOTALL)


def _parse_flash_response(data: Any) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    candidates = data.get("candidates") or []
    if not isinstance(candidates, list) or not candidates:
        return None
    candidate = candidates[0] if isinstance(candidates[0], dict) else {}
    content = candidate.get("content") if isinstance(candidate, dict) else None
    parts = content.get("parts") if isinstance(content, dict) else None
    if not isinstance(parts, list):
        return None
    raw_text = "\n".join(part.get("text", "") for part in parts if isinstance(part, dict)).strip()
    if not raw_text:
        return None
    try:
        decoded = json.loads(raw_text)
        if isinstance(decoded, dict) and "recommended" in decoded:
            return decoded
    except (TypeError, ValueError):
        pass
    matches = list(_FLASH_JSON_RE.finditer(raw_text))
    for match in reversed(matches):
        try:
            decoded = json.loads(match.group(0))
        except (TypeError, ValueError):
            continue
        if isinstance(decoded, dict) and "recommended" in decoded:
            return decoded
    return None


__all__ = [
    "BlendResult",
    "FlashReconciler",
    "blend",
    "blend_async",
]
