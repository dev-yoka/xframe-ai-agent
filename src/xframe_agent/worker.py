"""arq worker entry points for durable agent runs."""

from __future__ import annotations

from urllib.parse import urlparse

from arq.connections import RedisSettings, create_pool

from xframe_agent.agent.dispatch import execute_run
from xframe_agent.attachments import scan_bytes, storage_from_settings
from xframe_agent.auth.jwt import AuthContext
from xframe_agent.db.session import make_engine, make_session_factory
from xframe_agent.models import AgentAttachment
from xframe_agent.models.agent import utc_now
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
            await execute_run(session, settings=settings, run_id=run_id, context=auth_context)
    finally:
        await engine.dispose()


async def scan_attachment_job(_ctx: dict[str, object], *, attachment_id: str) -> None:
    """Scan one uploaded attachment and update its durable status."""

    settings = Settings()
    engine = make_engine(settings)
    session_factory = make_session_factory(engine)
    try:
        async with session_factory() as session:
            attachment = await session.get(AgentAttachment, attachment_id)
            if attachment is None:
                return
            data = await storage_from_settings(settings).get_bytes(key=attachment.storage_key)
            result = await scan_bytes(data, settings)
            attachment.scan_status = result.status
            attachment.scan_result = result.detail
            attachment.status = "ready" if result.is_clean else result.status
            attachment.updated_at = utc_now()
            await session.commit()
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
    functions = [run_agent_job, scan_attachment_job]
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


async def enqueue_attachment_scan(settings: Settings, *, attachment_id: str) -> None:
    """Enqueue one attachment scan on the configured arq queue."""

    redis = await create_pool(
        redis_settings_from_url(settings.redis_url),
        default_queue_name=settings.arq_queue_name,
    )
    try:
        job = await redis.enqueue_job("scan_attachment_job", attachment_id=attachment_id)
        if job is None:
            raise RuntimeError(f"Attachment scan already queued: {attachment_id}")
    finally:
        await redis.aclose()
