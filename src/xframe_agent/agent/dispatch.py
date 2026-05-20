"""Select and invoke the appropriate runner for one queued run.

When at least one LLM provider is configured we use :class:`ModelRunner` (the
LLM-driven loop). Otherwise we fall back to :class:`AgentLoop` (the
deterministic demo path).
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from xframe_agent.agent.history import load_history
from xframe_agent.agent.loop import AgentLoop
from xframe_agent.agent.runner import ModelRunner
from xframe_agent.auth.jwt import AuthContext
from xframe_agent.models import AgentRun
from xframe_agent.priceframe import PriceFrameClient
from xframe_agent.provider.factory import build_router
from xframe_agent.settings import Settings


async def execute_run(
    session: AsyncSession,
    *,
    settings: Settings,
    run_id: str,
    context: AuthContext,
) -> AgentRun:
    """Dispatch one run to the configured runner.

    For ``ModelRunner`` we load conversation history and build a
    ``ProviderFailoverRouter`` + ``PriceFrameClient`` per call. The runner
    persists run state directly on the session.
    """

    router = build_router(settings)
    if router is None:
        return await AgentLoop(settings).run(session, run_id=run_id, context=context)

    run = await session.get(AgentRun, run_id)
    if run is None:
        raise ValueError(f"Run not found: {run_id}")

    history = await load_history(session, conversation_id=run.conversation_id)

    async with PriceFrameClient.from_settings(settings) as priceframe:
        runner = ModelRunner(
            router=router,
            settings=settings,
            model=settings.default_model,
            priceframe_factory=priceframe,
        )
        return await runner.run(session, run=run, context=context, history=history)


__all__ = ["execute_run"]
