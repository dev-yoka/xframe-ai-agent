"""Anthropic fallback provider placeholder."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

from xframe_agent.provider.base import ChatMessage, ProviderError, StreamEvent
from xframe_agent.settings import Settings
from xframe_agent.tools.base import ToolDefinition


class AnthropicProvider:
    """Fallback provider adapter shell."""

    name = "anthropic"

    def __init__(self, settings: Settings) -> None:
        if not settings.anthropic_api_key:
            raise ProviderError("ANTHROPIC_API_KEY is not configured")
        self._api_key = settings.anthropic_api_key

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
        raise ProviderError("Anthropic SDK call is not wired in Phase D skeleton")
