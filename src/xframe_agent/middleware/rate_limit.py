"""Redis sliding-window rate limit middleware."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Any, cast

import redis.asyncio as redis
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from xframe_agent.settings import Settings

REDIS_SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local count = redis.call('ZCARD', key)
if count >= limit then
  return 0
end
redis.call('ZADD', key, now, member)
redis.call('EXPIRE', key, window)
return 1
"""


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Apply a per-client sliding-window request limit."""

    def __init__(self, app: ASGIApp, *, settings: Settings) -> None:
        super().__init__(app)
        self._settings = settings
        self._memory_hits: defaultdict[str, deque[float]] = defaultdict(deque)
        self._redis: redis.Redis | None = None
        if settings.rate_limit_backend == "redis":
            self._redis = cast(
                redis.Redis,
                redis.from_url(settings.redis_url, decode_responses=True),  # type: ignore[no-untyped-call]
            )

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        allowed = await self._allow_request(request)
        if not allowed:
            return JSONResponse(
                {"detail": "Rate limit exceeded"},
                status_code=429,
                headers={"Retry-After": str(self._settings.rate_limit_window_seconds)},
            )

        response = await call_next(request)
        return response

    async def _allow_request(self, request: Request) -> bool:
        key = self._rate_limit_key(request)
        if self._redis is not None:
            try:
                now_ms = int(time.time() * 1000)
                redis_client = cast(Any, self._redis)
                result = await redis_client.eval(
                    REDIS_SLIDING_WINDOW_LUA,
                    1,
                    key,
                    str(now_ms),
                    str(self._settings.rate_limit_window_seconds * 1000),
                    str(self._settings.rate_limit_requests),
                    f"{now_ms}:{id(request)}",
                )
                return result in (1, "1")
            except redis.RedisError:
                return self._allow_memory(key)
        return self._allow_memory(key)

    def _allow_memory(self, key: str) -> bool:
        now = time.monotonic()
        window_start = now - self._settings.rate_limit_window_seconds
        hits = self._memory_hits[key]
        while hits and hits[0] < window_start:
            hits.popleft()
        if len(hits) >= self._settings.rate_limit_requests:
            return False
        hits.append(now)
        return True

    @staticmethod
    def _rate_limit_key(request: Request) -> str:
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            client_id = forwarded_for.split(",", 1)[0].strip()
        elif request.client:
            client_id = request.client.host
        else:
            client_id = "unknown"
        return f"agent:rate:{client_id}:{request.url.path}"
