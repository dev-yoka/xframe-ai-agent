"""Prometheus metrics wiring."""

from __future__ import annotations

from fastapi import FastAPI
from prometheus_client import Counter, Histogram
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
