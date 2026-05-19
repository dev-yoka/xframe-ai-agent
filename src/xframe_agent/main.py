"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from xframe_agent import __version__
from xframe_agent.api.v1.router import router as v1_router
from xframe_agent.logging import setup_logging
from xframe_agent.middleware import RateLimitMiddleware, RequestIdMiddleware
from xframe_agent.observability import setup_metrics
from xframe_agent.settings import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the FastAPI application."""

    resolved_settings = settings or get_settings()
    setup_logging(resolved_settings)

    app = FastAPI(
        title="xFRAME Ai Agent API",
        version=__version__,
        description="AI agent service for PriceFRAME pricing workflows.",
        openapi_url=f"{resolved_settings.api_prefix}/openapi.json",
        docs_url=f"{resolved_settings.api_prefix}/docs",
        redoc_url=None,
    )
    app.state.settings = resolved_settings
    app.dependency_overrides[get_settings] = lambda: resolved_settings

    app.add_middleware(RequestIdMiddleware)
    if resolved_settings.rate_limit_enabled:
        app.add_middleware(RateLimitMiddleware, settings=resolved_settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved_settings.cors_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
    )

    app.include_router(v1_router, prefix=resolved_settings.api_prefix)
    setup_metrics(app, resolved_settings)
    return app


app = create_app()
