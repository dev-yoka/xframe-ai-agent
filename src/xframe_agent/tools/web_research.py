"""``WebResearchTool`` — Gemini grounding wrapper for market band suggestions.

Implements M2 Phase 9 (Wave C) of the milestone-2 design (`docs/superpowers/
specs/2026-05-24-milestone-2-full-pricing-coverage-design.md` §5.2). The tool
asks Gemini to research a corridor-/service-specific market price using its
built-in Google Search retrieval and returns a structured payload:

    {
      "value": float | None,
      "confidence": 0-1,
      "summary": str,
      "citations": [{"url", "title", "snippet"}],
      "cache_hit": bool,
    }

Failure modes are *graceful*: timeouts, missing API keys, and exhausted
:class:`RunBudget`s all return the empty-but-valid no-signal payload rather
than raising — the wizard simply degrades to historical-only.

Cache backend: prefers Redis (already wired in ``rate_limit`` middleware),
falls back to an in-process LRU keyed by ``sha1(query)``. The cache TTL is
controlled by the field's ``SuggestionSpec.market.max_age_seconds`` (default
6h per spec).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections import OrderedDict
from typing import Any, ClassVar

import httpx
from pydantic import BaseModel, Field

from xframe_agent.agent.suggestions_budget import RunBudget
from xframe_agent.auth.jwt import AuthContext
from xframe_agent.observability.metrics import observe_web_research_cost
from xframe_agent.priceframe import PriceFrameClient
from xframe_agent.settings import Settings
from xframe_agent.tools.base import ToolDefinition

logger = logging.getLogger(__name__)


EMPTY_SUMMARY = "research unavailable"


def _empty_payload(*, cache_hit: bool = False, summary: str = EMPTY_SUMMARY) -> dict[str, Any]:
    """No-signal payload — used for timeouts, budget exhaustion, and errors."""

    return {
        "value": None,
        "confidence": 0.0,
        "summary": summary,
        "citations": [],
        "cache_hit": cache_hit,
    }


def query_cache_key(query: str) -> str:
    """SHA1 cache key for a research query (matches spec §5.2)."""

    digest = hashlib.sha1(query.encode("utf-8"), usedforsecurity=False).hexdigest()
    return f"agent:market:{digest}"


class WebResearchInput(BaseModel):
    query: str = Field(min_length=1)
    context: dict[str, Any] = Field(default_factory=dict)
    max_age_seconds: int | None = Field(default=None, ge=1)


class WebResearchOutput(BaseModel):
    value: float | None = None
    confidence: float = 0.0
    summary: str = EMPTY_SUMMARY
    citations: list[dict[str, Any]] = Field(default_factory=list)
    cache_hit: bool = False


class ResearchCache:
    """Cache interface — Redis-backed when available, in-process fallback otherwise.

    The Redis backend uses ``setex`` so each query honours the field's
    ``max_age_seconds``. The in-process LRU is bounded so a long-running
    worker can't leak memory, and stores ``(expires_at_epoch, payload)``
    tuples to honour the same TTL contract.
    """

    _IN_PROCESS_MAX_SIZE: ClassVar[int] = 1024

    def __init__(self, *, redis_client: Any | None = None) -> None:
        self._redis = redis_client
        self._memory: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()

    async def get(self, key: str) -> dict[str, Any] | None:
        if self._redis is not None:
            try:
                raw = await self._redis.get(key)
            except Exception as exc:  # noqa: BLE001 - cache miss on any redis error
                logger.warning("research cache redis GET failed", extra={"error": str(exc)})
                raw = None
            if raw is not None:
                try:
                    payload = json.loads(raw)
                except (TypeError, ValueError):
                    return None
                if isinstance(payload, dict):
                    return payload
                return None
        entry = self._memory.get(key)
        if entry is None:
            return None
        expires_at, payload = entry
        if expires_at <= time.time():
            self._memory.pop(key, None)
            return None
        # LRU touch
        self._memory.move_to_end(key)
        return dict(payload)

    async def set(self, key: str, payload: dict[str, Any], ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            return
        if self._redis is not None:
            try:
                await self._redis.setex(key, ttl_seconds, json.dumps(payload))
                return
            except Exception as exc:  # noqa: BLE001 - degrade to in-memory
                logger.warning("research cache redis SET failed", extra={"error": str(exc)})
        self._memory[key] = (time.time() + ttl_seconds, dict(payload))
        self._memory.move_to_end(key)
        while len(self._memory) > self._IN_PROCESS_MAX_SIZE:
            self._memory.popitem(last=False)


class GeminiGroundingClient:
    """Thin HTTP client around Gemini's grounded ``generateContent`` endpoint.

    Kept separate from the streaming :mod:`xframe_agent.provider` adapters
    because grounding needs the **full response** (citations live in
    ``candidates[].groundingMetadata``) rather than an incremental SSE stream.
    Implementations may inject ``httpx.AsyncClient`` for tests.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        model: str | None = None,
    ) -> None:
        self._settings = settings
        self._client = client
        self._model = model or settings.web_research_model
        self._api_key = settings.gemini_developer_api_key
        self._base_url = settings.gemini_api_base_url.rstrip("/")

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    async def research(self, query: str, *, timeout_seconds: float) -> dict[str, Any]:
        """Issue one grounded ``generateContent`` call.

        Returns a payload shaped like :class:`WebResearchOutput` (sans
        ``cache_hit``). Raises ``asyncio.TimeoutError`` when the call breaches
        ``timeout_seconds`` — callers translate that into the graceful empty
        payload. Other transport errors are also re-raised; the tool catches
        ``Exception`` to keep one bad field from poisoning the fan-out.
        """

        if not self.configured:
            raise RuntimeError("Gemini API key not configured for web research")
        payload = _build_grounding_payload(query)
        url = f"{self._base_url}/models/{self._model}:generateContent"
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self._api_key or "",
        }
        async with asyncio.timeout(timeout_seconds):
            if self._client is not None:
                response = await self._client.post(url, headers=headers, json=payload)
            else:
                async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds)) as client:
                    response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return _parse_grounding_response(response.json())


def _build_grounding_payload(query: str) -> dict[str, Any]:
    """Construct the ``generateContent`` body with Google Search retrieval on.

    The system prompt forces a JSON-shaped reply so we can pull a structured
    ``{value, confidence, summary}`` out of the free-form text candidate while
    still benefitting from grounding (citations come from the response's
    ``groundingMetadata`` regardless of the body shape).
    """

    instruction = (
        "You are a pricing market-research assistant for cross-border remittance "
        "operators. Use Google Search grounding to find the current market price "
        "for the field described below. Respond with a single JSON object on the "
        'last line of your response containing the keys "value" (a number or null), '
        '"confidence" (0-1 number representing source quality + agreement), and '
        '"summary" (one sentence in plain English). Do not invent numbers — if no '
        'reliable source is found, set "value" to null and "confidence" to 0.\n\n'
        f"Field query: {query}\n\n"
        "Respond with the JSON object only on the final line."
    )
    return {
        "contents": [{"role": "user", "parts": [{"text": instruction}]}],
        "tools": [{"google_search_retrieval": {}}],
        "generationConfig": {"temperature": 0.0, "maxOutputTokens": 512},
    }


_JSON_OBJECT_RE = re.compile(r"\{[^{}]*\}", re.DOTALL)


def _parse_grounding_response(data: Any) -> dict[str, Any]:
    """Pull ``{value, confidence, summary, citations}`` out of the Gemini reply.

    Gemini's grounded response carries:
    - ``candidates[].content.parts[].text`` — the free-form answer
    - ``candidates[].groundingMetadata.groundingChunks[]`` — citation chunks
      with ``web.uri`` + ``web.title``
    - ``candidates[].groundingMetadata.groundingSupports[]`` — short snippets

    This parser is permissive: a malformed reply degrades to the empty
    payload rather than raising, so the calling fan-out can keep going.
    """

    if not isinstance(data, dict):
        return _empty_payload()
    candidates = data.get("candidates") or []
    if not isinstance(candidates, list) or not candidates:
        return _empty_payload()
    candidate = candidates[0] if isinstance(candidates[0], dict) else {}

    text_parts: list[str] = []
    content = candidate.get("content") if isinstance(candidate, dict) else None
    if isinstance(content, dict):
        for part in content.get("parts") or []:
            if isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    text_parts.append(text)
    raw_text = "\n".join(text_parts).strip()

    structured = _extract_structured_json(raw_text)
    value = structured.get("value") if isinstance(structured, dict) else None
    confidence = structured.get("confidence") if isinstance(structured, dict) else None
    summary = structured.get("summary") if isinstance(structured, dict) else None

    citations = _extract_citations(candidate)

    payload = _empty_payload()
    payload["citations"] = citations
    if isinstance(value, int | float):
        payload["value"] = float(value)
    if isinstance(confidence, int | float):
        try:
            payload["confidence"] = max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError):
            payload["confidence"] = 0.0
    if isinstance(summary, str) and summary.strip():
        payload["summary"] = summary.strip()
    elif raw_text:
        payload["summary"] = raw_text.splitlines()[0][:280]
    return payload


def _extract_structured_json(text: str) -> dict[str, Any]:
    """Pull the last JSON object out of a free-form text reply."""

    if not text:
        return {}
    matches = list(_JSON_OBJECT_RE.finditer(text))
    for match in reversed(matches):
        try:
            decoded = json.loads(match.group(0))
        except (TypeError, ValueError):
            continue
        if isinstance(decoded, dict):
            return decoded
    return {}


def _extract_citations(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    metadata = candidate.get("groundingMetadata") if isinstance(candidate, dict) else None
    if not isinstance(metadata, dict):
        return []
    chunks = metadata.get("groundingChunks") or []
    supports = metadata.get("groundingSupports") or []

    # Pre-compute snippets keyed by chunk index by walking supports.
    snippet_by_index: dict[int, str] = {}
    for support in supports:
        if not isinstance(support, dict):
            continue
        segment = support.get("segment") or {}
        snippet = segment.get("text") if isinstance(segment, dict) else None
        if not isinstance(snippet, str):
            continue
        for chunk_index in support.get("groundingChunkIndices") or []:
            if isinstance(chunk_index, int):
                snippet_by_index.setdefault(chunk_index, snippet)

    citations: list[dict[str, Any]] = []
    for idx, chunk in enumerate(chunks):
        if not isinstance(chunk, dict):
            continue
        web_raw = chunk.get("web")
        web: dict[str, Any] = web_raw if isinstance(web_raw, dict) else {}
        url = web.get("uri") if isinstance(web.get("uri"), str) else None
        title = web.get("title") if isinstance(web.get("title"), str) else None
        if not url:
            continue
        citations.append(
            {
                "url": url,
                "title": title or url,
                "snippet": snippet_by_index.get(idx, ""),
            }
        )
    return citations


class WebResearchTool(ToolDefinition[WebResearchInput, WebResearchOutput]):
    """Market band lookup via Gemini grounding.

    Per-field permission is shared with the historical band — anybody who can
    read historical suggestions can read market suggestions too. The tool is a
    READ in the risk taxonomy (no PriceFRAME state mutates) and rated
    ``medium`` cost because each call exercises an LLM provider.
    """

    name = "web_research"
    description = (
        "Research a market-price suggestion for a workflow field using Gemini "
        "grounding. Returns a structured suggestion with citations and a "
        "confidence score; degrades gracefully when budget/timeout hits."
    )
    input_model = WebResearchInput
    output_model = WebResearchOutput
    permission = "agent.suggestions.read"
    risk = "READ"
    cost_class = "medium"

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        cache: ResearchCache | None = None,
        client: GeminiGroundingClient | None = None,
        budget: RunBudget | None = None,
        timeout_seconds: float | None = None,
        default_ttl_seconds: int | None = None,
        estimated_cost_usd: float | None = None,
    ) -> None:
        # ``settings`` is optional so the tool can be registered before the
        # runtime container is wired; concrete dependencies are injected when
        # ``execute`` is called via the suggestions fan-out.
        self._settings = settings
        self._cache = cache or ResearchCache()
        self._client = client
        self._budget = budget
        self._timeout_seconds = timeout_seconds
        self._default_ttl_seconds = default_ttl_seconds
        self._estimated_cost_usd = estimated_cost_usd

    def with_runtime(
        self,
        *,
        settings: Settings,
        cache: ResearchCache,
        client: GeminiGroundingClient,
        budget: RunBudget,
    ) -> WebResearchTool:
        """Return a fan-out-scoped copy sharing the run-wide budget/cache."""

        return WebResearchTool(
            settings=settings,
            cache=cache,
            client=client,
            budget=budget,
            timeout_seconds=settings.web_research_timeout_seconds,
            default_ttl_seconds=settings.web_research_default_max_age_seconds,
            estimated_cost_usd=settings.web_research_estimated_cost_usd,
        )

    async def _execute(
        self,
        args: WebResearchInput,
        ctx: AuthContext,
        priceframe: PriceFrameClient,
    ) -> WebResearchOutput:
        del ctx, priceframe  # tool reaches Gemini, not PriceFRAME
        return WebResearchOutput(**await self._research(args))

    async def _research(self, args: WebResearchInput) -> dict[str, Any]:
        ttl_seconds = (
            args.max_age_seconds if args.max_age_seconds is not None else self._default_ttl_seconds
        )
        if ttl_seconds is None or ttl_seconds <= 0:
            ttl_seconds = 6 * 60 * 60
        cache_key = query_cache_key(args.query)

        cached = await self._cache.get(cache_key)
        if cached is not None:
            payload = dict(cached)
            payload["cache_hit"] = True
            return payload

        if self._client is None or not self._client.configured:
            return _empty_payload()

        timeout = (
            self._timeout_seconds
            if self._timeout_seconds is not None
            else (self._settings.web_research_timeout_seconds if self._settings else 5.0)
        )
        estimated_cost = (
            self._estimated_cost_usd
            if self._estimated_cost_usd is not None
            else (self._settings.web_research_estimated_cost_usd if self._settings else 0.01)
        )

        if self._budget is not None and not self._budget.can_spend(estimated_cost):
            logger.info(
                "web_research skipped — budget exhausted",
                extra={
                    "query_sha1": cache_key,
                    "budget": self._budget.snapshot(),
                },
            )
            return _empty_payload()

        try:
            result = await self._client.research(args.query, timeout_seconds=timeout)
        except TimeoutError:
            logger.info("web_research timed out", extra={"query_sha1": cache_key})
            return _empty_payload()
        except Exception as exc:  # noqa: BLE001 - tool stays graceful
            logger.warning(
                "web_research call failed",
                extra={"query_sha1": cache_key, "error": str(exc)},
            )
            return _empty_payload()

        if self._budget is not None:
            self._budget.record(estimated_cost)
        # Telemetry: record the estimated USD cost of every realised call so
        # the dashboard can plot ``rate(...) * sum_per_run`` for cost trend.
        observe_web_research_cost(estimated_cost)

        # Defensive copy + always-false cache_hit on a fresh call.
        payload = dict(result)
        payload.setdefault("value", None)
        payload.setdefault("confidence", 0.0)
        payload.setdefault("summary", EMPTY_SUMMARY)
        payload.setdefault("citations", [])
        payload["cache_hit"] = False

        # Only cache *successful* signals — empty payloads should be re-tried
        # next step entry in case grounding recovers.
        if payload.get("value") is not None or payload.get("confidence", 0) > 0:
            await self._cache.set(
                cache_key,
                {k: v for k, v in payload.items() if k != "cache_hit"},
                ttl_seconds,
            )
        return payload


__all__ = [
    "EMPTY_SUMMARY",
    "GeminiGroundingClient",
    "ResearchCache",
    "WebResearchInput",
    "WebResearchOutput",
    "WebResearchTool",
    "query_cache_key",
]
