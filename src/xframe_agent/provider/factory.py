"""Build provider failover routers from settings."""

from __future__ import annotations

from xframe_agent.provider.anthropic import AnthropicProvider
from xframe_agent.provider.base import Provider, ProviderFailoverRouter
from xframe_agent.provider.gemini_aistudio import GeminiAIStudioProvider
from xframe_agent.provider.gemini_vertex import GeminiVertexProvider
from xframe_agent.settings import Settings


def build_router(settings: Settings) -> ProviderFailoverRouter | None:
    """Construct a router from configured providers, in priority order.

    Priority order (Phase 11, M2-GA-02 — Vertex-primary):

    1. Gemini Vertex (production-grade, IAM/ADC-authenticated). Preferred
       whenever ``gemini_vertex_project`` is set.
    2. Gemini Developer API. Useful for local dev / staging where Vertex isn't
       wired but a quick API key is available.
    3. Anthropic. Final fallback whenever ``anthropic_api_key`` is configured.

    Returns ``None`` when no provider is configured — callers should fall back
    to the deterministic :class:`AgentLoop`.
    """

    providers: list[Provider] = []
    if settings.gemini_vertex_project:
        providers.append(GeminiVertexProvider(settings))
    if settings.gemini_developer_api_key:
        providers.append(GeminiAIStudioProvider(settings))
    if settings.anthropic_api_key:
        providers.append(AnthropicProvider(settings))
    if not providers:
        return None
    return ProviderFailoverRouter(providers=providers)


__all__ = ["build_router"]
