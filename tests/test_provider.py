"""Provider adapter guardrail tests."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx
import pytest
from google.genai import types as genai_types

from xframe_agent.provider import ProviderError
from xframe_agent.provider.base import (
    ChatMessage,
    ContentBlock,
    ProviderFailoverRouter,
    StreamEvent,
)
from xframe_agent.provider.factory import build_router
from xframe_agent.provider.gemini_aistudio import GeminiAIStudioProvider
from xframe_agent.provider.gemini_vertex import _function_declaration
from xframe_agent.settings import Settings
from xframe_agent.tools.base import ToolDefinition
from xframe_agent.tools.registry import tool_registry


class FailingProvider:
    def __init__(self, name: str, message: str) -> None:
        self.name = name
        self.message = message

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDefinition[Any, Any]],
        *,
        model: str,
        max_output_tokens: int,
    ) -> AsyncIterator[StreamEvent]:
        del messages, tools, model, max_output_tokens
        if self.message == "__yield__":
            yield StreamEvent(kind="unreachable")
        raise ProviderError(self.message)


def test_ai_studio_provider_refuses_real_data(test_settings: Settings) -> None:
    settings = test_settings.model_copy(
        update={"allow_real_data": True, "gemini_aistudio_api_key": "dev-key"}
    )

    with pytest.raises(ProviderError, match="ALLOW_REAL_DATA"):
        GeminiAIStudioProvider(settings)


def test_gemini_function_declaration_accepts_pydantic_json_schema() -> None:
    """Gemini declarations must accept Pydantic JSON Schema such as exclusiveMinimum."""

    tool = tool_registry.get("get_quotation")
    assert tool is not None

    declaration = _function_declaration(genai_types, tool)

    assert declaration.name == "get_quotation"
    assert declaration.parameters is None
    assert declaration.parameters_json_schema is not None
    assert declaration.parameters_json_schema["properties"]["id"]["exclusiveMinimum"] == 0


@pytest.mark.asyncio
async def test_failover_error_lists_provider_failures_in_order() -> None:
    router = ProviderFailoverRouter(
        providers=[
            FailingProvider("gemini-vertex", "ADC missing"),
            FailingProvider("anthropic", "credit balance is too low"),
        ]
    )

    with pytest.raises(ProviderError) as exc_info:
        async for _event in router.stream(
            [ChatMessage(role="user", content=[])],
            [],
            model="test-model",
            max_output_tokens=16,
        ):
            pass

    assert str(exc_info.value) == (
        "All providers failed: gemini-vertex: ADC missing; "
        "anthropic: credit balance is too low"
    )


@pytest.mark.asyncio
async def test_gemini_api_provider_calls_generate_content_with_api_key(
    test_settings: Settings,
) -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["api_key"] = request.headers.get("x-goog-api-key")
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [{"text": "Here are your open quotations."}],
                            "role": "model",
                        }
                    }
                ],
                "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 5},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = test_settings.model_copy(update={"gemini_api_key": "dev-api-key"})
    provider = GeminiAIStudioProvider(settings, client=client)

    events = [
        event
        async for event in provider.stream(
            [
                ChatMessage(
                    role="user",
                    content=[ContentBlock(type="text", payload={"text": "Show quotes"})],
                )
            ],
            [],
            model="gemini-2.5-flash",
            max_output_tokens=32,
        )
    ]
    await client.aclose()

    assert captured["url"] == (
        "https://generativelanguage.googleapis.com/v1beta/"
        "models/gemini-2.5-flash:generateContent"
    )
    assert captured["api_key"] == "dev-api-key"
    assert captured["payload"] == {
        "contents": [{"role": "user", "parts": [{"text": "Show quotes"}]}],
        "generationConfig": {"maxOutputTokens": 32},
    }
    assert events == [
        StreamEvent(kind="text_delta", payload={"delta": "Here are your open quotations."}),
        StreamEvent(kind="usage", payload={"input_tokens": 3, "output_tokens": 5}),
    ]


@pytest.mark.asyncio
async def test_gemini_api_provider_parses_function_calls(test_settings: Settings) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "functionCall": {
                                        "name": "list_my_quotations",
                                        "args": {"status": "open"},
                                    }
                                }
                            ]
                        }
                    }
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = test_settings.model_copy(update={"gemini_api_key": "dev-api-key"})
    provider = GeminiAIStudioProvider(settings, client=client)

    events = [
        event
        async for event in provider.stream(
            [ChatMessage(role="user", content=[ContentBlock(type="text", payload={"text": ""})])],
            [],
            model="gemini-2.5-flash",
            max_output_tokens=32,
        )
    ]
    await client.aclose()

    assert events == [
        StreamEvent(
            kind="tool_use",
            payload={
                "name": "list_my_quotations",
                "args": {"status": "open"},
                "call_id": "list_my_quotations",
            },
        )
    ]


@pytest.mark.asyncio
async def test_gemini_api_provider_owned_client_can_stream_twice(
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_async_client = httpx.AsyncClient
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {"content": {"parts": [{"text": f"response {calls}"}], "role": "model"}}
                ]
            },
        )

    def client_factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    provider = GeminiAIStudioProvider(test_settings.model_copy(update={"gemini_api_key": "key"}))

    first = [
        event
        async for event in provider.stream(
            [ChatMessage(role="user", content=[ContentBlock(type="text", payload={"text": "1"})])],
            [],
            model="gemini-2.5-flash",
            max_output_tokens=16,
        )
    ]
    second = [
        event
        async for event in provider.stream(
            [ChatMessage(role="user", content=[ContentBlock(type="text", payload={"text": "2"})])],
            [],
            model="gemini-2.5-flash",
            max_output_tokens=16,
        )
    ]

    assert calls == 2
    assert first == [StreamEvent(kind="text_delta", payload={"delta": "response 1"})]
    assert second == [StreamEvent(kind="text_delta", payload={"delta": "response 2"})]


def test_provider_factory_prefers_gemini_api_key_before_vertex_and_anthropic(
    test_settings: Settings,
) -> None:
    router = build_router(
        test_settings.model_copy(
            update={
                "gemini_api_key": "dev-api-key",
                "gemini_vertex_project": "vertex-project",
                "anthropic_api_key": "anthropic-key",
            }
        )
    )

    assert router is not None
    assert [provider.name for provider in router.providers] == [
        "gemini-api",
        "gemini-vertex",
        "anthropic",
    ]
