"""Health endpoint."""

from __future__ import annotations

from typing import Annotated, Any, Literal, cast

import httpx
import redis.asyncio as redis
from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field

from xframe_agent.db.session import check_database
from xframe_agent.settings import Settings, get_settings

router = APIRouter(tags=["health"])
SettingsDep = Annotated[Settings, Depends(get_settings)]


class ComponentHealth(BaseModel):
    """Health state for one dependency."""

    status: Literal["ok", "degraded", "skipped"]
    detail: str | None = None


class HealthResponse(BaseModel):
    """Aggregated health response."""

    status: Literal["ok", "degraded"] = "ok"
    checks: dict[str, ComponentHealth] = Field(default_factory=dict)


@router.get("/health", response_model=HealthResponse)
async def health(
    response: Response,
    settings: SettingsDep,
) -> HealthResponse:
    """Check service dependencies."""

    checker = HealthChecker(settings)
    result = await checker.check()
    if result.status != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return result


class HealthChecker:
    """Dependency health checker."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def check(self) -> HealthResponse:
        checks = {
            "provider": self._provider_health(),
            "database": await self._database_health(),
            "queue": await self._redis_health(),
            "priceframe": await self._priceframe_health(),
        }
        overall: Literal["ok", "degraded"] = (
            "degraded" if any(check.status == "degraded" for check in checks.values()) else "ok"
        )
        return HealthResponse(status=overall, checks=checks)

    def _provider_health(self) -> ComponentHealth:
        if self._settings.provider_configured:
            return ComponentHealth(status="ok")
        return ComponentHealth(status="skipped", detail="No production LLM provider configured")

    async def _database_health(self) -> ComponentHealth:
        if not self._settings.health_check_externals:
            return ComponentHealth(status="skipped", detail="External health checks disabled")
        try:
            await check_database(self._settings)
        except Exception as exc:
            return ComponentHealth(status="degraded", detail=str(exc))
        return ComponentHealth(status="ok")

    async def _redis_health(self) -> ComponentHealth:
        if not self._settings.health_check_externals:
            return ComponentHealth(status="skipped", detail="External health checks disabled")
        client = cast(Any, redis.from_url(self._settings.redis_url))  # type: ignore[no-untyped-call]
        try:
            await client.ping()
        except redis.RedisError as exc:
            return ComponentHealth(status="degraded", detail=str(exc))
        finally:
            await client.aclose()
        return ComponentHealth(status="ok")

    async def _priceframe_health(self) -> ComponentHealth:
        if not self._settings.health_check_externals:
            return ComponentHealth(status="skipped", detail="External health checks disabled")
        try:
            async with httpx.AsyncClient(
                base_url=self._settings.priceframe_base_url.rstrip("/"),
                timeout=httpx.Timeout(self._settings.priceframe_timeout_seconds),
            ) as client:
                response = await client.get("/api/google-drive/health")
        except httpx.HTTPError as exc:
            return ComponentHealth(status="degraded", detail=str(exc))

        if response.status_code >= 500:
            return ComponentHealth(
                status="degraded",
                detail=f"PriceFRAME health returned HTTP {response.status_code}",
            )
        return ComponentHealth(status="ok")
