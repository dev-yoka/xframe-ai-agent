"""Build provider failover routers from settings."""

from __future__ import annotations

from xframe_agent.provider.anthropic import AnthropicProvider
from xframe_agent.provider.base import Provider, ProviderFailoverRouter
from xframe_agent.provider.gemini_vertex import GeminiVertexProvider
from xframe_agent.settings import Settings


def build_router(settings: Settings) -> ProviderFailoverRouter | None:
    """Construct a router from configured providers, in priority order.

    Returns ``None`` when no provider is configured — callers should fall back
    to the deterministic :class:`AgentLoop`.

    Order: Gemini Vertex (primary) → Anthropic (fallback). To use a different
    order or different providers, edit this function.
    """

    providers: list[Provider] = []
    if settings.gemini_vertex_project:
        providers.append(GeminiVertexProvider(settings))
    if settings.anthropic_api_key:
        providers.append(AnthropicProvider(settings))
    if not providers:
        return None
    return ProviderFailoverRouter(providers=providers)


__all__ = ["build_router"]
