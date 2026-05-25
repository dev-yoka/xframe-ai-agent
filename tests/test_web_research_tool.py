"""Tests for ``WebResearchTool`` (M2 Phase 9 / Wave C).

The tool wraps Gemini grounding behind a tight contract: timeouts, budget
exhaustion, missing API keys, and malformed responses all return the same
no-signal payload. The tests exercise every degradation branch with a
fake ``GeminiGroundingClient`` and an in-process :class:`ResearchCache`.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from xframe_agent.agent.suggestions_budget import RunBudget
from xframe_agent.settings import Settings
from xframe_agent.tools.web_research import (
    EMPTY_SUMMARY,
    GeminiGroundingClient,
    ResearchCache,
    WebResearchInput,
    WebResearchTool,
    _parse_grounding_response,
    query_cache_key,
)


def _settings(**overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        "gemini_api_key": "fake-grounding-key",
        "web_research_timeout_seconds": 0.5,
        "web_research_default_max_age_seconds": 3600,
        "web_research_estimated_cost_usd": 0.01,
        "max_research_calls_per_run": 5,
        "max_research_cost_per_run_usd": 0.05,
    }
    defaults.update(overrides)
    return Settings(**defaults)


class _FakeClient:
    """In-memory stand-in for :class:`GeminiGroundingClient`."""

    configured = True

    def __init__(
        self,
        *,
        result: dict[str, Any] | None = None,
        sleep_seconds: float | None = None,
        error: Exception | None = None,
    ) -> None:
        self.calls: list[str] = []
        self._result = result
        self._sleep_seconds = sleep_seconds
        self._error = error

    async def research(self, query: str, *, timeout_seconds: float) -> dict[str, Any]:
        self.calls.append(query)
        if self._sleep_seconds is not None:
            await asyncio.sleep(self._sleep_seconds)
        if self._error is not None:
            raise self._error
        if self._result is None:
            return {
                "value": None,
                "confidence": 0.0,
                "summary": EMPTY_SUMMARY,
                "citations": [],
            }
        return dict(self._result)


def _make_tool(client: _FakeClient, *, budget: RunBudget | None = None) -> WebResearchTool:
    return WebResearchTool(
        settings=_settings(),
        cache=ResearchCache(),
        client=client,  # type: ignore[arg-type]
        budget=budget or RunBudget(),
        timeout_seconds=0.5,
        default_ttl_seconds=3600,
        estimated_cost_usd=0.01,
    )


async def test_returns_structured_signal_on_success() -> None:
    client = _FakeClient(
        result={
            "value": 1.42,
            "confidence": 0.8,
            "summary": "Market median for USA→IND remittance fee.",
            "citations": [
                {"url": "https://example.com/a", "title": "Source A", "snippet": "snippet"}
            ],
        }
    )
    tool = _make_tool(client)
    payload = await tool._research(
        WebResearchInput(query="USA to IND remittance fee 2026", context={})
    )
    assert payload["value"] == 1.42
    assert payload["confidence"] == 0.8
    assert payload["citations"][0]["url"] == "https://example.com/a"
    assert payload["cache_hit"] is False
    assert client.calls == ["USA to IND remittance fee 2026"]


async def test_timeout_returns_graceful_empty_payload() -> None:
    # Use a slow client paired with a 50ms timeout: the asyncio.timeout in the
    # client wrapper raises, the tool converts it into the empty payload.
    client = _FakeClient(sleep_seconds=1.0)

    async def _raise_timeout(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise TimeoutError

    client.research = _raise_timeout  # type: ignore[method-assign]
    tool = _make_tool(client)
    payload = await tool._research(WebResearchInput(query="anything"))
    assert payload == {
        "value": None,
        "confidence": 0.0,
        "summary": EMPTY_SUMMARY,
        "citations": [],
        "cache_hit": False,
    }


async def test_budget_exhaustion_returns_empty_without_calling_client() -> None:
    client = _FakeClient(result={"value": 1.0, "confidence": 0.9, "summary": "x", "citations": []})
    budget = RunBudget(max_calls=0, max_cost_usd=0.05)
    tool = _make_tool(client, budget=budget)
    payload = await tool._research(WebResearchInput(query="anything"))
    assert payload["value"] is None
    assert payload["summary"] == EMPTY_SUMMARY
    assert client.calls == []  # never reached


async def test_cache_hit_does_not_call_client_again() -> None:
    cache = ResearchCache()
    client = _FakeClient(
        result={
            "value": 2.5,
            "confidence": 0.7,
            "summary": "first call",
            "citations": [{"url": "https://x", "title": "x", "snippet": ""}],
        }
    )
    tool = WebResearchTool(
        settings=_settings(),
        cache=cache,
        client=client,  # type: ignore[arg-type]
        budget=RunBudget(),
        timeout_seconds=0.5,
        default_ttl_seconds=3600,
        estimated_cost_usd=0.01,
    )

    first = await tool._research(WebResearchInput(query="repeat-me"))
    assert first["cache_hit"] is False
    assert first["value"] == 2.5

    second = await tool._research(WebResearchInput(query="repeat-me"))
    assert second["cache_hit"] is True
    assert second["value"] == 2.5
    assert len(client.calls) == 1


async def test_missing_api_key_returns_empty_without_calling_client() -> None:
    client = _FakeClient(result={"value": 1, "confidence": 1, "summary": "ok", "citations": []})
    client.configured = False
    tool = WebResearchTool(
        settings=_settings(),
        cache=ResearchCache(),
        client=client,  # type: ignore[arg-type]
        budget=RunBudget(),
        timeout_seconds=0.5,
        default_ttl_seconds=3600,
        estimated_cost_usd=0.01,
    )
    payload = await tool._research(WebResearchInput(query="ignored"))
    assert payload["value"] is None
    assert client.calls == []


async def test_unexpected_error_returns_empty_payload() -> None:
    client = _FakeClient(error=RuntimeError("boom"))
    tool = _make_tool(client)
    payload = await tool._research(WebResearchInput(query="anything"))
    assert payload["value"] is None
    assert payload["confidence"] == 0.0
    assert payload["citations"] == []


async def test_citations_pass_through_into_payload() -> None:
    client = _FakeClient(
        result={
            "value": 3.0,
            "confidence": 0.9,
            "summary": "summary",
            "citations": [
                {"url": "https://one", "title": "One", "snippet": "first"},
                {"url": "https://two", "title": "Two", "snippet": "second"},
            ],
        }
    )
    tool = _make_tool(client)
    payload = await tool._research(WebResearchInput(query="ctest"))
    assert payload["citations"] == [
        {"url": "https://one", "title": "One", "snippet": "first"},
        {"url": "https://two", "title": "Two", "snippet": "second"},
    ]


async def test_parse_grounding_response_extracts_value_and_citations() -> None:
    raw = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": (
                                "Based on the sources, the median fee is around 1.5%. "
                                '{"value": 1.5, "confidence": 0.8, '
                                '"summary": "Median fee USA->IND."}'
                            )
                        }
                    ]
                },
                "groundingMetadata": {
                    "groundingChunks": [
                        {"web": {"uri": "https://wise.com/blog", "title": "Wise blog"}},
                        {"web": {"uri": "https://remitly.com/pricing", "title": "Remitly"}},
                    ],
                    "groundingSupports": [
                        {
                            "segment": {"text": "Median fee is around 1.5%."},
                            "groundingChunkIndices": [0, 1],
                        }
                    ],
                },
            }
        ]
    }
    parsed = _parse_grounding_response(raw)
    assert parsed["value"] == 1.5
    assert parsed["confidence"] == 0.8
    assert parsed["summary"] == "Median fee USA->IND."
    assert len(parsed["citations"]) == 2
    assert parsed["citations"][0] == {
        "url": "https://wise.com/blog",
        "title": "Wise blog",
        "snippet": "Median fee is around 1.5%.",
    }


async def test_parse_grounding_response_degrades_on_unknown_payload() -> None:
    assert _parse_grounding_response({})["value"] is None
    assert _parse_grounding_response({"candidates": []})["value"] is None
    assert _parse_grounding_response(None)["value"] is None


def test_query_cache_key_is_stable() -> None:
    key_a = query_cache_key("USA-IND fee 2026")
    key_b = query_cache_key("USA-IND fee 2026")
    key_c = query_cache_key("USA-IND fee 2027")
    assert key_a == key_b
    assert key_a != key_c
    assert key_a.startswith("agent:market:")


async def test_gemini_grounding_client_refuses_without_api_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    # Strip every env var that could populate ``gemini_developer_api_key``
    # and point the env file at an empty tmp file so .env doesn't bleed in.
    for var in ("GEMINI_API_KEY", "GEMINI_AISTUDIO_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    empty_env = tmp_path / "empty.env"
    empty_env.write_text("")
    monkeypatch.chdir(tmp_path)
    settings = Settings(gemini_api_key=None, gemini_aistudio_api_key=None)
    client = GeminiGroundingClient(settings)
    assert client.configured is False
    with pytest.raises(RuntimeError):
        await client.research("q", timeout_seconds=0.1)
