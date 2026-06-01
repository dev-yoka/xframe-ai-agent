"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request

from xframe_agent import __version__
from xframe_agent.api.v1.router import router as v1_router
from xframe_agent.db.session import make_engine, make_session_factory
from xframe_agent.logging import setup_logging
from xframe_agent.middleware import RateLimitMiddleware, RequestIdMiddleware
from xframe_agent.observability import setup_metrics
from xframe_agent.schemas.errors import ErrorDetail, ErrorResponse
from xframe_agent.settings import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the FastAPI application."""

    resolved_settings = settings or get_settings()
    setup_logging(resolved_settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await app.state.engine.dispose()

    app = FastAPI(
        title="xFRAME Ai Agent API",
        version=__version__,
        description="AI agent service for PriceFRAME pricing workflows.",
        openapi_url=f"{resolved_settings.api_prefix}/openapi.json",
        docs_url=f"{resolved_settings.api_prefix}/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.dependency_overrides[get_settings] = lambda: resolved_settings
    app.state.engine = make_engine(resolved_settings)
    app.state.session_factory = make_session_factory(app.state.engine)

    app.add_middleware(RequestIdMiddleware)
    if resolved_settings.rate_limit_enabled:
        app.add_middleware(RateLimitMiddleware, settings=resolved_settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved_settings.cors_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        # Last-Event-ID is sent by browsers on SSE reconnects — must be explicitly allowed.
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID", "Last-Event-ID"],
    )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=ErrorResponse(
                error=ErrorDetail(
                    code="validation_error",
                    message="Request validation failed",
                    detail=str(exc.errors()),
                )
            ).model_dump(),
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorResponse(
                error=ErrorDetail(code=f"http_{exc.status_code}", message=str(exc.detail))
            ).model_dump(),
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                error=ErrorDetail(code="internal_error", message="An unexpected error occurred")
            ).model_dump(),
        )

    app.include_router(v1_router, prefix=resolved_settings.api_prefix)
    setup_metrics(app, resolved_settings)
    return app


app = create_app()
