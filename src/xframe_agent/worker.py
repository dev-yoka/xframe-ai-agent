"""arq worker entry points for durable agent runs."""

from __future__ import annotations

from urllib.parse import urlparse

from arq.connections import RedisSettings, create_pool

from xframe_agent.agent.loop import AgentLoop
from xframe_agent.auth.jwt import AuthContext
from xframe_agent.db.session import make_engine, make_session_factory
from xframe_agent.settings import Settings


async def run_agent_job(
    _ctx: dict[str, object],
    *,
    run_id: str,
    user_id: int,
    role_code: str,
    profile_code: str,
    permissions: list[str],
    jwt_raw: str,
    session_id: int | None,
) -> None:
    """Execute one queued agent run."""

    settings = Settings()
    engine = make_engine(settings)
    session_factory = make_session_factory(engine)
    auth_context = AuthContext(
        user_id=user_id,
        role_code=role_code,
        profile_code=profile_code,
        permissions=tuple(permissions),
        jwt_raw=jwt_raw,
        session_id=session_id,
    )
    try:
        async with session_factory() as session:
            await AgentLoop().run(session, run_id=run_id, context=auth_context)
    finally:
        await engine.dispose()


def redis_settings_from_url(redis_url: str) -> RedisSettings:
    """Convert a redis:// URL into arq settings."""

    parsed = urlparse(redis_url)
    database = int(parsed.path.lstrip("/") or "0")
    return RedisSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        database=database,
        username=parsed.username,
        password=parsed.password,
        ssl=parsed.scheme == "rediss",
    )


class WorkerSettings:
    """Settings object consumed by `arq xframe_agent.worker.WorkerSettings`."""

    settings = Settings()
    functions = [run_agent_job]
    redis_settings = redis_settings_from_url(settings.redis_url)
    queue_name = settings.arq_queue_name
    max_jobs = 4


async def enqueue_agent_run(
    settings: Settings,
    *,
    run_id: str,
    auth_context: AuthContext,
) -> None:
    """Enqueue one run on the configured arq queue."""

    redis = await create_pool(
        redis_settings_from_url(settings.redis_url),
        default_queue_name=settings.arq_queue_name,
    )
    try:
        job = await redis.enqueue_job(
            "run_agent_job",
            run_id=run_id,
            user_id=auth_context.user_id,
            role_code=auth_context.role_code,
            profile_code=auth_context.profile_code,
            permissions=list(auth_context.permissions),
            jwt_raw=auth_context.jwt_raw,
            session_id=auth_context.session_id,
        )
        if job is None:
            raise RuntimeError(f"Run already queued: {run_id}")
    finally:
        await redis.aclose()
