"""Pytest gate for the suggestion-quality eval (M2-OBSERVE-02).

Runs the full 50-quote × 11-field eval (550 samples) hermetically and asserts
the overall ``within ±25%`` ratio stays above the declared baseline. The
report files are regenerated on every run so a CI artifact captures the
fresh numbers alongside the test verdict.

The eval is intentionally not parametrized per-field — that would make
flaky failures harder to diagnose because the blender is global per-fan-out.
Treat the report files (``report.md``, ``report.csv``) as the artefact when
the gate fails.
"""

from __future__ import annotations

import pytest

from evals.suggestion_quality.eval import (
    BASELINE_WITHIN_25,
    overall_within_25,
    run_eval,
)


@pytest.mark.asyncio
async def test_suggestion_quality_meets_baseline() -> None:
    results, report = await run_eval()
    assert results, "Eval produced zero samples — fixture broken?"
    accuracy = overall_within_25(report, len(results))
    assert accuracy >= BASELINE_WITHIN_25, (
        f"Suggestion-quality eval regressed: "
        f"overall within ±25% = {accuracy:.1%}, baseline = {BASELINE_WITHIN_25:.0%}. "
        f"See evals/suggestion_quality/report.md for per-field breakdown."
    )


@pytest.mark.asyncio
async def test_suggestion_quality_no_field_in_total_collapse() -> None:
    """Even on a weak field, ≥ 30% of samples must land within ±25%.

    Single-field collapse would suggest a contract / filter_keys mismatch
    rather than overall blender drift, so this catches that class of bug
    before it ships.
    """

    _, report = await run_eval()
    weak_fields: list[str] = []
    for field_id, rec in report.items():
        if rec.samples == 0:
            continue
        if rec.accuracy_within_25 < 0.30:
            weak_fields.append(f"{field_id} ({rec.within_25}/{rec.samples} within ±25%)")
    assert not weak_fields, (
        "One or more fields collapsed below the per-field floor (30% within ±25%): "
        + ", ".join(weak_fields)
    )
