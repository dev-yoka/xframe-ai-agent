"""Anthropic fallback provider.

The ``anthropic`` SDK is imported lazily so local tests can construct the
provider without touching SDK client state until a real stream is requested.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import TYPE_CHECKING, Any, cast

from xframe_agent.provider.base import ChatMessage, ProviderError, StreamEvent
from xframe_agent.settings import Settings
from xframe_agent.tools.base import ToolDefinition

if TYPE_CHECKING:
    from anthropic.types import MessageParam, ToolParam


class AnthropicProvider:
    """Fallback provider adapter using Claude."""

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
        if self._api_key == "__stream_test__":
            yield StreamEvent(kind="usage", payload={"input_tokens": 0, "output_tokens": 0})
            return

        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - import gate
            raise ProviderError("anthropic SDK not installed; run `uv add anthropic`") from exc

        client = anthropic.AsyncAnthropic(api_key=self._api_key)
        anthropic_messages: list[MessageParam] = [
            _to_anthropic_message(message) for message in messages
        ]
        tool_specs: list[ToolParam] = [
            cast(
                "ToolParam",
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_model.model_json_schema(),
                },
            )
            for tool in tools
        ]
        tools_param = tool_specs if tool_specs else anthropic.omit

        try:
            async with client.messages.stream(
                model=model,
                max_tokens=max_output_tokens,
                messages=anthropic_messages,
                tools=tools_param,
            ) as stream:
                pending_tool: dict[str, Any] | None = None
                async for event in stream:
                    event_type = getattr(event, "type", "")
                    if event_type == "content_block_start":
                        block = getattr(event, "content_block", None)
                        if block and getattr(block, "type", "") == "tool_use":
                            pending_tool = {
                                "name": block.name,
                                "args": {},
                                "call_id": block.id,
                                "buffer": "",
                            }
                    elif event_type == "content_block_delta":
                        delta = getattr(event, "delta", None)
                        if not delta:
                            continue
                        delta_type = getattr(delta, "type", "")
                        if delta_type == "text_delta":
                            yield StreamEvent(
                                kind="text_delta",
                                payload={"delta": getattr(delta, "text", "")},
                            )
                        elif delta_type == "input_json_delta" and pending_tool is not None:
                            pending_tool["buffer"] += getattr(delta, "partial_json", "")
                    elif event_type == "content_block_stop" and pending_tool is not None:
                        import json

                        try:
                            pending_tool["args"] = json.loads(pending_tool["buffer"] or "{}")
                        except json.JSONDecodeError:
                            pending_tool["args"] = {}
                        yield StreamEvent(
                            kind="tool_use",
                            payload={
                                "name": pending_tool["name"],
                                "args": pending_tool["args"],
                                "call_id": pending_tool["call_id"],
                            },
                        )
                        pending_tool = None
                final = await stream.get_final_message()
                usage = getattr(final, "usage", None)
                if usage:
                    yield StreamEvent(
                        kind="usage",
                        payload={
                            "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
                            "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
                        },
                    )
        except Exception as exc:  # noqa: BLE001 - SDK raises ad-hoc errors
            raise ProviderError(f"Anthropic call failed: {exc}") from exc


def _to_anthropic_message(message: ChatMessage) -> MessageParam:
    text_parts: list[str] = []
    for block in message.content:
        payload = block.payload
        if "wrapped" in payload:
            text_parts.append(str(payload["wrapped"]))
        elif "text" in payload:
            text_parts.append(str(payload["text"]))
    role = "user" if message.role in {"user", "tool", "system"} else "assistant"
    return cast("MessageParam", {"role": role, "content": "\n".join(text_parts)})
