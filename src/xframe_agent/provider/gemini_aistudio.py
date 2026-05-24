"""Gemini Developer API adapter using an API key."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

import httpx

from xframe_agent.provider.base import ChatMessage, ProviderError, StreamEvent
from xframe_agent.settings import Settings
from xframe_agent.tools.base import ToolDefinition

_TRANSIENT_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
_MAX_RETRY_DELAY_SECONDS = 5.0


class GeminiAIStudioProvider:
    """Gemini API-key provider for local/dev use."""

    name = "gemini-api"

    def __init__(self, settings: Settings, *, client: httpx.AsyncClient | None = None) -> None:
        if settings.allow_real_data:
            raise ProviderError("AI Studio provider refuses to start when ALLOW_REAL_DATA=true")
        if not settings.gemini_developer_api_key:
            raise ProviderError("GEMINI_API_KEY is not configured")
        self._api_key = settings.gemini_developer_api_key
        self._base_url = settings.gemini_api_base_url.rstrip("/")
        self._max_retries = max(0, settings.gemini_api_max_retries)
        self._retry_base_delay_seconds = max(0.0, settings.gemini_api_retry_base_delay_seconds)
        self._client = client

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

        payload = _request_payload(messages, tools, max_output_tokens=max_output_tokens)
        response: httpx.Response
        try:
            if self._client is not None:
                response = await self._post_generate_content_with_retries(
                    self._client, model, payload
                )
            else:
                async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                    response = await self._post_generate_content_with_retries(
                        client, model, payload
                    )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(f"Gemini API call failed: {_response_error(exc.response)}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"Gemini API call failed: {exc}") from exc

        data = response.json()
        for event in _events_from_response(data):
            yield event

    async def _post_generate_content(
        self,
        client: httpx.AsyncClient,
        model: str,
        payload: dict[str, Any],
    ) -> httpx.Response:
        return await client.post(
            f"{self._base_url}/models/{model}:generateContent",
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": self._api_key,
            },
            json=payload,
        )

    async def _post_generate_content_with_retries(
        self,
        client: httpx.AsyncClient,
        model: str,
        payload: dict[str, Any],
    ) -> httpx.Response:
        attempts = self._max_retries + 1
        response: httpx.Response | None = None
        for attempt_index in range(attempts):
            response = await self._post_generate_content(client, model, payload)
            if response.status_code not in _TRANSIENT_STATUS_CODES:
                return response
            if attempt_index == attempts - 1:
                return response
            await asyncio.sleep(
                _retry_delay_seconds(
                    response,
                    attempt_index=attempt_index,
                    base_delay_seconds=self._retry_base_delay_seconds,
                )
            )
        if response is None:
            raise ProviderError("Gemini API call failed before sending a request")
        return response


def _request_payload(
    messages: Sequence[ChatMessage],
    tools: Sequence[ToolDefinition[Any, Any]],
    *,
    max_output_tokens: int,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "contents": [_content_from_message(message) for message in messages],
        "generationConfig": {"maxOutputTokens": max_output_tokens},
    }
    if tools:
        payload["tools"] = [
            {
                "functionDeclarations": [
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "parametersJsonSchema": tool.input_model.model_json_schema(),
                    }
                    for tool in tools
                ]
            }
        ]
    return payload


def _content_from_message(message: ChatMessage) -> dict[str, Any]:
    return {
        "role": "model" if message.role == "assistant" else "user",
        "parts": [{"text": _message_text(message)}],
    }


def _message_text(message: ChatMessage) -> str:
    parts: list[str] = []
    for block in message.content:
        payload = block.payload
        if "wrapped" in payload:
            parts.append(str(payload["wrapped"]))
        elif "text" in payload:
            parts.append(str(payload["text"]))
    return "\n".join(parts)


def _events_from_response(data: Mapping[str, Any]) -> list[StreamEvent]:
    events: list[StreamEvent] = []
    for candidate in _list(data.get("candidates")):
        content = _mapping(candidate.get("content"))
        for part in _list(content.get("parts")):
            text = part.get("text")
            if isinstance(text, str) and text:
                events.append(StreamEvent(kind="text_delta", payload={"delta": text}))
            function_call = _mapping(part.get("functionCall"))
            name = function_call.get("name")
            if isinstance(name, str) and name:
                args = function_call.get("args")
                events.append(
                    StreamEvent(
                        kind="tool_use",
                        payload={
                            "name": name,
                            "args": args if isinstance(args, dict) else {},
                            "call_id": str(function_call.get("id") or name),
                        },
                    )
                )

    usage = _mapping(data.get("usageMetadata"))
    if usage:
        events.append(
            StreamEvent(
                kind="usage",
                payload={
                    "input_tokens": _int_value(usage.get("promptTokenCount")),
                    "output_tokens": _int_value(usage.get("candidatesTokenCount")),
                },
            )
        )
    return events


def _response_error(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text or f"HTTP {response.status_code}"
    error = _mapping(payload.get("error"))
    message = error.get("message")
    if isinstance(message, str):
        return message
    return f"HTTP {response.status_code}"


def _retry_delay_seconds(
    response: httpx.Response,
    *,
    attempt_index: int,
    base_delay_seconds: float,
) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after is not None:
        try:
            retry_after_seconds = float(retry_after)
            if retry_after_seconds > _MAX_RETRY_DELAY_SECONDS:
                return _MAX_RETRY_DELAY_SECONDS
            return retry_after_seconds
        except ValueError:
            pass
    exponential_delay = float(base_delay_seconds * (2**attempt_index))
    if exponential_delay > _MAX_RETRY_DELAY_SECONDS:
        return _MAX_RETRY_DELAY_SECONDS
    return exponential_delay


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _list(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, Mapping)]
    return []


def _int_value(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return 0
