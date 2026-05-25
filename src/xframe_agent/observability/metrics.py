"""Prometheus metrics wiring.

Houses both the API-level histograms/counters (run latency, step count, run
errors) and the M2-OBSERVE-* wizard telemetry added in Phase 10:

* ``agent_workflow_step_duration_seconds`` — Histogram[step_id, outcome] —
  measured between ``v1.workflow.step.entered`` and the next terminal event
  for that step (``advance_approved`` / ``advance_blocked`` /
  ``advance_rejected`` / ``abandoned``).
* ``agent_suggestion_sources_used`` — Counter[source] — incremented per
  fan-out per source used (``historical`` / ``market`` / ``blended`` /
  ``flash``). Low-cardinality by design.
* ``agent_suggestion_no_signal_total`` — Counter[field_id] — incremented when
  the blend returns no_signal for a field. ``field_id`` is bounded by the
  workflow contract so cardinality stays small.
* ``agent_draft_resumes_total`` — Counter — incremented on
  ``GET /conversations/{id}/draft`` when an existing draft is served.
* ``agent_web_research_cost_usd`` — Summary — observed once per
  :class:`WebResearchTool` call (estimated USD cost from settings).
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from typing import Any

from fastapi import FastAPI
from prometheus_client import Counter, Histogram, Summary
from prometheus_fastapi_instrumentator import Instrumentator

from xframe_agent.settings import Settings

AGENT_RUN_LATENCY_SECONDS = Histogram(
    "agent_run_latency_seconds",
    "Wall-clock latency for agent runs.",
    ["model"],
)
AGENT_STEP_COUNT = Histogram(
    "agent_step_count",
    "Number of loop steps per agent run.",
)
AGENT_RUN_ERRORS_TOTAL = Counter(
    "agent_run_errors_total",
    "Agent runs that ended in an error.",
    ["cause"],
)

# --- M2 Phase 10 (Wave D / M2-OBSERVE-*) -----------------------------------

WORKFLOW_STEP_DURATION_SECONDS = Histogram(
    "agent_workflow_step_duration_seconds",
    "Seconds between entering a wizard step and resolving it.",
    ["step_id", "outcome"],
    # Wizard step durations span a few seconds (auto-advance) to minutes
    # (filling 30 fields). Default histogram buckets are too coarse for the
    # short tail, so use explicit buckets that match the spec funnel SLOs.
    buckets=(0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0, 1800.0),
)

SUGGESTION_SOURCES_USED_TOTAL = Counter(
    "agent_suggestion_sources_used",
    "Suggestion fan-out outcomes by source (historical/market/blended/flash).",
    ["source"],
)

SUGGESTION_NO_SIGNAL_TOTAL = Counter(
    "agent_suggestion_no_signal_total",
    "Field-level no_signal results from the suggestion blender.",
    ["field_id"],
)

DRAFT_RESUMES_TOTAL = Counter(
    "agent_draft_resumes_total",
    "Workflow draft resume hits (GET /draft returned an existing draft).",
)

WEB_RESEARCH_COST_USD = Summary(
    "agent_web_research_cost_usd",
    "Estimated USD cost per web research (Gemini grounding) call.",
)


def setup_metrics(app: FastAPI, settings: Settings) -> None:
    """Attach Prometheus instrumentation when enabled."""

    if not settings.prometheus_enabled:
        return
    Instrumentator(
        should_group_status_codes=True,
        should_ignore_untemplated=True,
        excluded_handlers=[f"{settings.api_prefix}/openapi.json"],
    ).instrument(app).expose(
        app,
        endpoint=f"{settings.api_prefix}/metrics",
        include_in_schema=False,
    )


def observe_run_latency(*, model: str, seconds: float) -> None:
    """Record one completed run latency."""

    AGENT_RUN_LATENCY_SECONDS.labels(model=model).observe(seconds)


def observe_step_count(count: int) -> None:
    """Record one run's step count."""

    AGENT_STEP_COUNT.observe(count)


def increment_run_error(cause: str) -> None:
    """Record one terminal run error."""

    AGENT_RUN_ERRORS_TOTAL.labels(cause=cause).inc()


# --- Wave D helpers --------------------------------------------------------


_VALID_STEP_OUTCOMES: frozenset[str] = frozenset({"approved", "blocked", "rejected", "abandoned"})


def observe_workflow_step_duration(*, step_id: str, outcome: str, seconds: float) -> None:
    """Record one workflow step's wall-clock duration.

    ``outcome`` is constrained to the four labels declared in the spec so the
    histogram stays low cardinality. Unknown outcomes fall back to "abandoned"
    so a typo in the caller can't accidentally explode label cardinality.
    """

    safe_outcome = outcome if outcome in _VALID_STEP_OUTCOMES else "abandoned"
    if seconds < 0:
        seconds = 0.0
    WORKFLOW_STEP_DURATION_SECONDS.labels(step_id=step_id, outcome=safe_outcome).observe(seconds)


def increment_suggestion_sources(sources: Iterable[str]) -> None:
    """Increment per-source counters for one fan-out result.

    Pass the ``sources_used`` field from a :class:`BlendResult` (or any
    iterable of source labels). Empty iterables are no-ops — callers do not
    need to special-case that.
    """

    for source in sources:
        if not isinstance(source, str) or not source:
            continue
        SUGGESTION_SOURCES_USED_TOTAL.labels(source=source).inc()


def increment_suggestion_no_signal(field_id: str) -> None:
    """Increment the no_signal counter for ``field_id``."""

    if not isinstance(field_id, str) or not field_id:
        return
    SUGGESTION_NO_SIGNAL_TOTAL.labels(field_id=field_id).inc()


def increment_draft_resume() -> None:
    """Increment the draft-resume counter (one per served GET /draft)."""

    DRAFT_RESUMES_TOTAL.inc()


def observe_web_research_cost(cost_usd: float) -> None:
    """Record one web-research call's estimated USD cost."""

    if cost_usd is None or cost_usd < 0:
        return
    WEB_RESEARCH_COST_USD.observe(float(cost_usd))


class WorkflowStepTimer:
    """Context-manager-style timer for workflow step durations.

    Used by the runner / step_advance handler to record one duration sample
    when a step ends. The caller is expected to set ``outcome`` before exit
    via :meth:`set_outcome`; defaulting to ``abandoned`` covers the case
    where the timer is dropped (e.g. an exception bubbles out).
    """

    __slots__ = ("step_id", "started_at", "_outcome", "_recorded")

    def __init__(self, *, step_id: str) -> None:
        self.step_id = step_id
        self.started_at = time.monotonic()
        self._outcome = "abandoned"
        self._recorded = False

    def set_outcome(self, outcome: str) -> None:
        self._outcome = outcome

    def record(self) -> None:
        if self._recorded:
            return
        self._recorded = True
        elapsed = max(0.0, time.monotonic() - self.started_at)
        observe_workflow_step_duration(step_id=self.step_id, outcome=self._outcome, seconds=elapsed)

    def __enter__(self) -> WorkflowStepTimer:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.record()


__all__ = [
    "AGENT_RUN_ERRORS_TOTAL",
    "AGENT_RUN_LATENCY_SECONDS",
    "AGENT_STEP_COUNT",
    "DRAFT_RESUMES_TOTAL",
    "SUGGESTION_NO_SIGNAL_TOTAL",
    "SUGGESTION_SOURCES_USED_TOTAL",
    "WEB_RESEARCH_COST_USD",
    "WORKFLOW_STEP_DURATION_SECONDS",
    "WorkflowStepTimer",
    "increment_draft_resume",
    "increment_run_error",
    "increment_suggestion_no_signal",
    "increment_suggestion_sources",
    "observe_run_latency",
    "observe_step_count",
    "observe_web_research_cost",
    "observe_workflow_step_duration",
    "setup_metrics",
]
