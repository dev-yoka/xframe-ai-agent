"""Vertex Gemini adapter placeholder for the production primary provider."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

from xframe_agent.provider.base import ChatMessage, ProviderError, StreamEvent
from xframe_agent.settings import Settings
from xframe_agent.tools.base import ToolDefinition


class GeminiVertexProvider:
    """Primary real-data provider adapter shell."""

    name = "gemini-vertex"

    def __init__(self, settings: Settings) -> None:
        if not settings.gemini_vertex_project:
            raise ProviderError("GEMINI_VERTEX_PROJECT is not configured")
        self._project = settings.gemini_vertex_project
        self._location = settings.gemini_vertex_location

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDefinition[Any, Any]],
        *,
        model: str,
        max_output_tokens: int,
    ) -> AsyncIterator[StreamEvent]:
        del messages, tools, model, max_output_tokens
        if self._project == "__stream_test__":
            yield StreamEvent(kind="usage", payload={})
        raise ProviderError("Vertex Gemini SDK call is not wired in Phase D skeleton")
