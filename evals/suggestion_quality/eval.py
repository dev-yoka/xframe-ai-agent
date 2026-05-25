"""Suggestion-quality eval (M2-OBSERVE-02).

Walk the 50-quote fixture, mask one suggestable field per quote, replay the
agent's blender against an in-process historical endpoint computed from the
remaining 49 quotes, and tally accuracy buckets per field. Emits both a CSV
and a Markdown summary so the report is greppable + diffable.

Hermetic: the PriceFrame stub re-uses the contract's median aggregation on
the historical subset, so the eval needs no network. The market band is
deliberately disabled (no Gemini key) — this eval measures the historical
+ blend layer; the market band has its own integration tests under
``tests/test_web_research_tool.py``.

The eval enforces a ship-readiness threshold: at least ``BASELINE_WITHIN_25``
percent of (quote, field) samples must land within ±25% of the true value.
The threshold is set slightly below the measured baseline so a small drift
trips the test before we ship a regression.
"""

from __future__ import annotations

import asyncio
import csv
import json
import statistics
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from xframe_agent.agent.suggestions import fan_out_suggestions
from xframe_agent.auth.jwt import AuthContext

FIXTURE_PATH = Path(__file__).parent / "quotes_fixture.json"
REPORT_DIR = Path(__file__).parent

# Tolerance band thresholds (relative).
WITHIN_10 = 0.10
WITHIN_25 = 0.25

# Baseline: at least 80% of samples must land within ±25%. The measured
# blender-on-fixture baseline is ~90% (with 8% locked into no_signal because
# the two low-data corridors have only one peer quote after hold-out), so
# this leaves ~10pp of headroom before the test starts failing. Update only
# when the blender intentionally changes shape (and document why in the commit).
BASELINE_WITHIN_25 = 0.80


@dataclass
class SampleResult:
    quote_id: int
    corridor: str
    service: str
    field_id: str
    actual: float
    predicted: float | None
    sources_used: list[str]
    no_signal: bool
    bucket: str  # within_10 | within_25 | outside_25 | no_signal

    def to_row(self) -> dict[str, Any]:
        return {
            "quote_id": self.quote_id,
            "corridor": self.corridor,
            "service": self.service,
            "field_id": self.field_id,
            "actual": self.actual,
            "predicted": self.predicted,
            "sources_used": "|".join(self.sources_used),
            "no_signal": "true" if self.no_signal else "false",
            "bucket": self.bucket,
        }


@dataclass
class FieldReport:
    field_id: str
    samples: int = 0
    within_10: int = 0
    within_25: int = 0
    outside_25: int = 0
    no_signal: int = 0
    abs_errors: list[float] = field(default_factory=list)

    @property
    def accuracy_within_25(self) -> float:
        if self.samples == 0:
            return 0.0
        return self.within_25 / self.samples

    @property
    def median_abs_error_pct(self) -> float:
        if not self.abs_errors:
            return 0.0
        return statistics.median(self.abs_errors)


def _bucket(actual: float, predicted: float | None) -> str:
    if predicted is None or actual == 0:
        return "no_signal" if predicted is None else "outside_25"
    rel = abs(actual - predicted) / abs(actual)
    if rel <= WITHIN_10:
        return "within_10"
    if rel <= WITHIN_25:
        return "within_25"
    return "outside_25"


def _make_contract(field_id: str) -> dict[str, Any]:
    """Tiny single-field, single-step contract — enough to drive the fan-out."""

    return {
        "id": "create_pricing_request",
        "version": "v1",
        "title": "Create pricing request",
        "steps": [
            {
                "id": "pricing",
                "title": "Pricing",
                "description": "eval",
                "approval_mode": "batch_at_submit",
                "fields": [
                    {
                        "id": field_id,
                        "label": field_id,
                        "type": "currency",
                        "required": False,
                        "suggestion": {
                            "mode": "proactive",
                            "sources": ["historical"],
                            "historical": {
                                "aggregation": "median",
                                "filter_keys": ["corridor", "service"],
                                "min_sample_size": 3,
                            },
                        },
                    }
                ],
            }
        ],
    }


class HistoricalStub:
    """In-process replacement for PriceFrameClient's agent suggestions endpoint.

    Computes a median over the supplied corpus filtered by the ctx keys. When
    the filtered subset is smaller than min_sample_size, returns a no_signal
    payload mirroring the real ``/api/v1/agent/suggestions`` shape.
    """

    def __init__(self, corpus: list[dict[str, Any]], *, field_id: str) -> None:
        self._corpus = corpus
        self._field_id = field_id
        # Calls intentionally counted so the eval report can show per-field
        # call counts if needed during debugging.
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def get_json(
        self,
        path: str,
        *,
        jwt_raw: str,  # noqa: ARG002 - unused in stub
        params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        params = dict(params or {})
        self.calls.append((path, params))
        field_id = params.get("field")
        if field_id != self._field_id:
            return {"value": None, "no_signal": True}
        ctx_raw = params.get("ctx") or ""
        ctx = _decode_ctx(ctx_raw)
        subset = [
            q for q in self._corpus
            if all(q.get(k) == v for k, v in ctx.items())
        ]
        values = [q["fields"].get(field_id) for q in subset if field_id in q["fields"]]
        values = [v for v in values if isinstance(v, int | float)]
        if len(values) < 3:
            return {
                "value": None,
                "unit": "PCT_AMOUNT",
                "sample_size": len(values),
                "range": None,
                "basis": {"aggregation": "median", "filter_keys": list(ctx.keys())},
                "context_used": ctx,
                "as_of": "2026-05-24T00:00:00Z",
                "no_signal": True,
            }
        sorted_values = sorted(values)
        return {
            "value": statistics.median(sorted_values),
            "unit": "PCT_AMOUNT",
            "sample_size": len(values),
            "range": {
                "min": sorted_values[0],
                "max": sorted_values[-1],
                "p25": _percentile(sorted_values, 0.25),
                "p75": _percentile(sorted_values, 0.75),
            },
            "basis": {"aggregation": "median", "filter_keys": list(ctx.keys())},
            "context_used": ctx,
            "as_of": "2026-05-24T00:00:00Z",
        }


def _percentile(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    idx = max(0, min(len(sorted_values) - 1, int(round(p * (len(sorted_values) - 1)))))
    return sorted_values[idx]


def _decode_ctx(ctx_raw: Any) -> dict[str, Any]:
    """Re-decode the agent's base64-JSON ctx envelope.

    GetFieldSuggestionsTool sends ``ctx`` as a base64-encoded JSON object; in
    eval mode we want to short-circuit that wrapping so the harness stays
    readable. The agent passes the same dict via plain JSON when the request
    flows through the in-process stub, so accept either shape here.
    """

    if isinstance(ctx_raw, dict):
        return dict(ctx_raw)
    if isinstance(ctx_raw, str) and ctx_raw:
        try:
            import base64

            decoded = base64.b64decode(ctx_raw.encode("ascii"))
            parsed = json.loads(decoded.decode("utf-8"))
            if isinstance(parsed, dict):
                return parsed
        except Exception:  # noqa: BLE001
            return {}
    return {}


_AUTH = AuthContext(
    user_id=1,
    role_code="ROLE_AM_SALES",
    profile_code="PROFILE_SALES",
    permissions=("agent.enabled", "agent.suggestions.read"),
    jwt_raw="eval-jwt",
    session_id=1,
)


async def _evaluate_one(
    *,
    quote: dict[str, Any],
    field_id: str,
    corpus: list[dict[str, Any]],
) -> SampleResult | None:
    """Mask ``field_id`` on ``quote`` and ask the blender to recover it."""

    actual = quote["fields"].get(field_id)
    if actual is None or not isinstance(actual, int | float):
        return None
    # Hold-out: exclude the target quote from the corpus so we measure
    # generalisation, not memorisation.
    other_quotes = [q for q in corpus if q["quote_id"] != quote["quote_id"]]
    stub = HistoricalStub(other_quotes, field_id=field_id)
    contract = _make_contract(field_id)
    draft_state = {
        "summary": {
            "corridor": quote["corridor"],
            "service": quote["service"],
            "customer_segment": quote["customer_segment"],
        }
    }
    events = await fan_out_suggestions(
        contract=contract,
        step=contract["steps"][0],
        draft_state=draft_state,
        auth_ctx=_AUTH,
        priceframe=stub,  # type: ignore[arg-type]
    )
    event = next((e for e in events if e["payload"].get("field_id") == field_id), None)
    if event is None:
        return None
    payload = event["payload"]
    no_signal = event["event_type"] == "v1.suggestion.no_signal"
    proposed = payload.get("proposed") if isinstance(payload, dict) else None
    predicted: float | None
    sources_used: list[str]
    if no_signal:
        predicted = None
        sources_used = []
    elif isinstance(proposed, dict):
        proposed_value = proposed.get("value")
        predicted = proposed_value if isinstance(proposed_value, int | float) else None
        sources_used = list(proposed.get("sources_used") or [])
    else:
        raw_value = payload.get("value")
        predicted = raw_value if isinstance(raw_value, int | float) else None
        sources_used = ["historical"] if predicted is not None else []

    bucket = "no_signal" if no_signal else _bucket(float(actual), predicted)
    return SampleResult(
        quote_id=quote["quote_id"],
        corridor=quote["corridor"],
        service=quote["service"],
        field_id=field_id,
        actual=float(actual),
        predicted=float(predicted) if predicted is not None else None,
        sources_used=sources_used,
        no_signal=no_signal,
        bucket=bucket,
    )


async def run_eval(
    *,
    fixture_path: Path = FIXTURE_PATH,
    write_reports: bool = True,
) -> tuple[list[SampleResult], dict[str, FieldReport]]:
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    quotes: list[dict[str, Any]] = fixture["quotes"]
    fields: list[str] = fixture["suggestable_fields"]

    results: list[SampleResult] = []
    for quote in quotes:
        for field_id in fields:
            sample = await _evaluate_one(quote=quote, field_id=field_id, corpus=quotes)
            if sample is not None:
                results.append(sample)

    report: dict[str, FieldReport] = defaultdict(lambda: FieldReport(field_id=""))
    for sample in results:
        rec = report[sample.field_id]
        rec.field_id = sample.field_id
        rec.samples += 1
        if sample.bucket == "within_10":
            rec.within_10 += 1
            rec.within_25 += 1
        elif sample.bucket == "within_25":
            rec.within_25 += 1
        elif sample.bucket == "outside_25":
            rec.outside_25 += 1
        elif sample.bucket == "no_signal":
            rec.no_signal += 1
        if sample.predicted is not None and sample.actual != 0:
            rel_err = abs(sample.actual - sample.predicted) / abs(sample.actual)
            rec.abs_errors.append(rel_err)

    if write_reports:
        _write_csv(results)
        _write_markdown(report, len(results))
    return results, dict(report)


def _write_csv(results: list[SampleResult]) -> None:
    path = REPORT_DIR / "report.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "quote_id",
                "corridor",
                "service",
                "field_id",
                "actual",
                "predicted",
                "sources_used",
                "no_signal",
                "bucket",
            ],
        )
        writer.writeheader()
        for sample in results:
            writer.writerow(sample.to_row())


def _write_markdown(report: dict[str, FieldReport], total_samples: int) -> None:
    path = REPORT_DIR / "report.md"
    lines: list[str] = []
    lines.append("# Suggestion Quality Eval Report")
    lines.append("")
    lines.append(f"Total samples: {total_samples}")
    overall_within_25 = (
        sum(r.within_25 for r in report.values()) / total_samples
        if total_samples
        else 0.0
    )
    lines.append(f"Overall within ±25%: {overall_within_25:.1%}")
    lines.append(f"Baseline gate: {BASELINE_WITHIN_25:.0%}")
    lines.append("")
    header = (
        "| Field | Samples | Within ±10% | Within ±25% | Outside ±25% "
        "| No signal | Median rel. error |"
    )
    lines.append(header)
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for field_id in sorted(report):
        rec = report[field_id]
        median_err = rec.median_abs_error_pct
        lines.append(
            f"| {field_id} | {rec.samples} | {rec.within_10} | {rec.within_25} | "
            f"{rec.outside_25} | {rec.no_signal} | {median_err:.1%} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def overall_within_25(report: Mapping[str, FieldReport], total_samples: int) -> float:
    if total_samples == 0:
        return 0.0
    return sum(r.within_25 for r in report.values()) / total_samples


def run_eval_sync() -> tuple[list[SampleResult], dict[str, FieldReport]]:
    return asyncio.run(run_eval())


if __name__ == "__main__":
    results, report = run_eval_sync()
    accuracy = overall_within_25(report, len(results))
    print(f"Overall within ±25%: {accuracy:.1%} (baseline {BASELINE_WITHIN_25:.0%})")
    for field_id in sorted(report):
        rec = report[field_id]
        print(
            f"  {field_id}: {rec.within_25}/{rec.samples} within ±25%, "
            f"{rec.no_signal} no_signal"
        )
