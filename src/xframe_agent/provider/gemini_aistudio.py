"""Google AI Studio Gemini adapter for synthetic dev fixtures only."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

from xframe_agent.provider.base import ChatMessage, ProviderError, StreamEvent
from xframe_agent.settings import Settings
from xframe_agent.tools.base import ToolDefinition


class GeminiAIStudioProvider:
    """Development-only Gemini provider gated away from real data."""

    name = "gemini-aistudio"

    def __init__(self, settings: Settings) -> None:
        if settings.allow_real_data:
            raise ProviderError("AI Studio provider refuses to start when ALLOW_REAL_DATA=true")
        if not settings.gemini_aistudio_api_key:
            raise ProviderError("GEMINI_AISTUDIO_API_KEY is not configured")
        self._api_key = settings.gemini_aistudio_api_key

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDefinition[Any, Any]],
        *,
        model: str,
        max_output_tokens: int,
    ) -> AsyncIterator[StreamEvent]:
        del messages, tools, model, max_output_tokens
        if self._api_key == "__stream_test__":
            yield StreamEvent(kind="usage", payload={})
        raise ProviderError("Gemini AI Studio SDK call is not wired in Phase D skeleton")
