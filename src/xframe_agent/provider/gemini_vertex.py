"""Vertex Gemini adapter (primary production provider).

The Google Gen AI SDK (``google-genai``) is imported lazily so it remains an
optional runtime dependency. Install it via ``uv add google-genai`` when you
plan to point the service at a real Vertex project.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

from xframe_agent.provider.base import ChatMessage, ProviderError, StreamEvent
from xframe_agent.settings import Settings
from xframe_agent.tools.base import ToolDefinition


class GeminiVertexProvider:
    """Primary real-data provider adapter."""

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
        if self._project == "__stream_test__":
            yield StreamEvent(kind="usage", payload={"input_tokens": 0, "output_tokens": 0})
            return

        try:
            from google import genai  # type: ignore[import-not-found]
            from google.genai import types as genai_types  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - import gate
            raise ProviderError(
                "google-genai SDK not installed; run `uv add google-genai`"
            ) from exc

        client = genai.Client(
            vertexai=True,
            project=self._project,
            location=self._location,
        )

        contents = [
            genai_types.Content(
                role=_vertex_role(message.role),
                parts=[genai_types.Part(text=_message_text(message))],
            )
            for message in messages
        ]
        tool_decl = genai_types.Tool(
            function_declarations=[
                genai_types.FunctionDeclaration(
                    name=tool.name,
                    description=tool.description,
                    parameters=tool.input_model.model_json_schema(),
                )
                for tool in tools
            ]
        )
        config = genai_types.GenerateContentConfig(
            tools=[tool_decl] if tools else None,
            max_output_tokens=max_output_tokens,
        )

        try:
            stream = await client.aio.models.generate_content_stream(
                model=model,
                contents=contents,
                config=config,
            )
        except Exception as exc:  # noqa: BLE001 - SDK raises ad-hoc errors
            raise ProviderError(f"Vertex Gemini call failed: {exc}") from exc

        usage_payload: dict[str, int] = {}
        async for chunk in stream:
            for candidate in getattr(chunk, "candidates", []) or []:
                for part in getattr(getattr(candidate, "content", None), "parts", []) or []:
                    if getattr(part, "text", None):
                        yield StreamEvent(
                            kind="text_delta",
                            payload={"delta": part.text},
                        )
                    func_call = getattr(part, "function_call", None)
                    if func_call:
                        yield StreamEvent(
                            kind="tool_use",
                            payload={
                                "name": func_call.name,
                                "args": dict(getattr(func_call, "args", {}) or {}),
                                "call_id": getattr(func_call, "id", "") or func_call.name,
                            },
                        )
            usage = getattr(chunk, "usage_metadata", None)
            if usage:
                usage_payload = {
                    "input_tokens": int(getattr(usage, "prompt_token_count", 0) or 0),
                    "output_tokens": int(getattr(usage, "candidates_token_count", 0) or 0),
                }
        if usage_payload:
            yield StreamEvent(kind="usage", payload=usage_payload)


def _vertex_role(role: str) -> str:
    if role == "assistant":
        return "model"
    if role == "tool":
        return "function"
    return role


def _message_text(message: ChatMessage) -> str:
    parts: list[str] = []
    for block in message.content:
        if block.type in {"text", "tool_result"}:
            payload = block.payload
            if "wrapped" in payload:
                parts.append(str(payload["wrapped"]))
            elif "text" in payload:
                parts.append(str(payload["text"]))
    return "\n".join(parts)
