"""Prometheus metrics wiring."""

from __future__ import annotations

from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

from xframe_agent.settings import Settings


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
