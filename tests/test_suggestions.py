"""Tests for the proactive historical suggestion fan-out (M2-Phase-08 Wave B.2)
plus the M2 Phase 9 / Wave C blending + market band paths."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

import xframe_agent.models  # noqa: F401
from xframe_agent.agent.events import list_run_events
from xframe_agent.agent.suggestions import (
    EVENT_NO_SIGNAL,
    EVENT_READY,
    build_suggestion_ctx,
    emit_historical_suggestions,
    fan_out_historical_suggestions,
    fan_out_suggestions,
)
from xframe_agent.agent.suggestions_blend import (
    BlendResult,
    FlashReconciler,
    blend,
    blend_async,
)
from xframe_agent.agent.suggestions_budget import RunBudget
from xframe_agent.auth.jwt import AuthContext
from xframe_agent.db.base import Base
from xframe_agent.models import AgentConversation, AgentRun
from xframe_agent.settings import Settings
from xframe_agent.tools.web_research import ResearchCache

_AUTH = AuthContext(
    user_id=1,
    role_code="ROLE_AM_SALES",
    profile_code="PROFILE_SALES",
    permissions=(
        "agent.enabled",
        "agent.suggestions.read",
    ),
    jwt_raw="test-jwt",
    session_id=1,
)


def _make_step(
    fields: list[dict[str, Any]],
    *,
    step_id: str = "pricing",
) -> dict[str, Any]:
    return {
        "id": step_id,
        "title": "Pricing",
        "description": "Pricing fields",
        "approval_mode": "batch_at_submit",
        "fields": fields,
    }


def _make_contract(steps: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": "create_pricing_request",
        "version": "v1",
        "title": "Create pricing request",
        "steps": steps,
    }


def _proactive_field(
    field_id: str,
    *,
    filter_keys: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": field_id,
        "label": field_id,
        "type": "currency",
        "required": False,
        "suggestion": {
            "mode": "proactive",
            "sources": ["historical"],
            "historical": {
                "aggregation": "median",
                "filter_keys": filter_keys or ["corridor", "service"],
                "min_sample_size": 3,
            },
        },
    }


class _StubPriceFrame:
    """Records calls and returns scripted responses keyed by field id."""

    def __init__(self, responses: Mapping[str, Any]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, Mapping[str, Any] | None]] = []

    async def get_json(
        self,
        path: str,
        *,
        jwt_raw: str,  # noqa: ARG002
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        self.calls.append((path, dict(params or {})))
        if params is None:
            return {}
        field = params.get("field")
        if not isinstance(field, str):
            return {}
        response = self._responses.get(field)
        if isinstance(response, Exception):
            raise response
        return response if response is not None else {}


@pytest.fixture
async def session_factory(tmp_path: Path) -> AsyncIterator[tuple[AsyncEngine, Any, str]]:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'suggestions.db'}"
    engine = create_async_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        conv = AgentConversation(user_id=1, title="t", kind="create_pricing_request")
        session.add(conv)
        await session.flush()
        run = AgentRun(conversation_id=conv.id, user_id=1, status="running")
        session.add(run)
        await session.commit()
        run_id = run.id
    try:
        yield engine, factory, run_id
    finally:
        await engine.dispose()


def test_build_suggestion_ctx_picks_only_filter_keys() -> None:
    draft = {
        "summary": {
            "corridor": "USA-IND",
            "service": "C2C",
            "customer_segment": "RETAIL",
            "unused": "ignored",
        },
    }
    assert build_suggestion_ctx(draft, ["corridor", "service"]) == {
        "corridor": "USA-IND",
        "service": "C2C",
    }


def test_build_suggestion_ctx_skips_missing_and_empty() -> None:
    draft = {"summary": {"corridor": "USA-IND", "service": "", "customer_segment": None}}
    assert build_suggestion_ctx(draft, ["corridor", "service", "customer_segment"]) == {
        "corridor": "USA-IND",
    }


def test_build_suggestion_ctx_handles_empty_draft() -> None:
    assert build_suggestion_ctx(None, ["corridor"]) == {}
    assert build_suggestion_ctx({}, ["corridor"]) == {}


async def test_fanout_empty_when_no_proactive_historical_fields() -> None:
    step = _make_step(
        [
            {
                "id": "manual_only",
                "label": "Manual only",
                "type": "currency",
                "required": False,
            },
            {
                "id": "reactive_field",
                "label": "Reactive",
                "type": "currency",
                "required": False,
                "suggestion": {
                    "mode": "reactive",
                    "sources": ["historical"],
                    "historical": {
                        "aggregation": "median",
                        "filter_keys": ["corridor"],
                        "min_sample_size": 3,
                    },
                },
            },
            {
                "id": "market_only",
                "label": "Market only",
                "type": "currency",
                "required": False,
                "suggestion": {
                    "mode": "proactive",
                    "sources": ["market"],
                    "market": {
                        "research_query_template": "irrelevant",
                        "max_age_seconds": 600,
                    },
                },
            },
        ]
    )
    contract = _make_contract([step])
    priceframe = _StubPriceFrame({})

    events = await fan_out_historical_suggestions(
        contract=contract,
        step=step,
        draft_state={"summary": {}},
        auth_ctx=_AUTH,
        priceframe=priceframe,  # type: ignore[arg-type]
    )

    assert events == []
    assert priceframe.calls == []


async def test_fanout_emits_ready_for_each_proactive_field() -> None:
    step = _make_step(
        [
            _proactive_field("default_transaction_fee", filter_keys=["corridor", "service"]),
            _proactive_field("fx_spread_bps", filter_keys=["corridor"]),
        ]
    )
    contract = _make_contract([step])
    responses = {
        "default_transaction_fee": {
            "value": 1.5,
            "unit": "PCT_AMOUNT",
            "sample_size": 12,
            "range": {"min": 0.5, "max": 3.0, "p25": 1.0, "p75": 2.0},
            "basis": {"aggregation": "median", "filter_keys": ["corridor", "service"]},
            "context_used": {"corridor": "USA-IND", "service": "C2C"},
            "as_of": "2026-05-24T00:00:00Z",
        },
        "fx_spread_bps": {
            "value": 25,
            "unit": "BPS",
            "sample_size": 7,
            "range": {"min": 10, "max": 40, "p25": 18, "p75": 32},
            "basis": {"aggregation": "median", "filter_keys": ["corridor"]},
            "context_used": {"corridor": "USA-IND"},
            "as_of": "2026-05-24T00:00:00Z",
        },
    }
    priceframe = _StubPriceFrame(responses)

    events = await fan_out_historical_suggestions(
        contract=contract,
        step=step,
        draft_state={"summary": {"corridor": "USA-IND", "service": "C2C"}},
        auth_ctx=_AUTH,
        priceframe=priceframe,  # type: ignore[arg-type]
    )

    assert len(events) == 2
    by_field = {event["payload"]["field_id"]: event for event in events}
    fee_event = by_field["default_transaction_fee"]
    assert fee_event["event_type"] == EVENT_READY
    assert fee_event["payload"]["value"] == 1.5
    assert fee_event["payload"]["unit"] == "PCT_AMOUNT"
    assert fee_event["payload"]["sample_size"] == 12
    assert fee_event["payload"]["range"]["p75"] == 2.0
    assert fee_event["payload"]["step_id"] == "pricing"
    assert fee_event["payload"]["contract_id"] == "create_pricing_request"
    assert fee_event["payload"]["contract_version"] == "v1"
    assert fee_event["payload"]["context_used"] == {
        "corridor": "USA-IND",
        "service": "C2C",
    }

    fx_event = by_field["fx_spread_bps"]
    assert fx_event["event_type"] == EVENT_READY
    # The fx field only filters by corridor; service must be stripped before the
    # tool call.
    fx_calls = [c for c in priceframe.calls if c[1] and c[1].get("field") == "fx_spread_bps"]
    assert len(fx_calls) == 1


async def test_fanout_emits_no_signal_when_endpoint_signals_no_signal() -> None:
    step = _make_step([_proactive_field("default_transaction_fee")])
    contract = _make_contract([step])
    priceframe = _StubPriceFrame(
        {
            "default_transaction_fee": {
                "value": None,
                "unit": None,
                "sample_size": 1,
                "range": None,
                "basis": {"aggregation": "median", "filter_keys": ["corridor", "service"]},
                "context_used": {"corridor": "USA-IND"},
                "as_of": "2026-05-24T00:00:00Z",
                "no_signal": True,
            }
        }
    )

    events = await fan_out_historical_suggestions(
        contract=contract,
        step=step,
        draft_state={"summary": {"corridor": "USA-IND", "service": "C2C"}},
        auth_ctx=_AUTH,
        priceframe=priceframe,  # type: ignore[arg-type]
    )

    assert len(events) == 1
    event = events[0]
    assert event["event_type"] == EVENT_NO_SIGNAL
    assert event["payload"]["field_id"] == "default_transaction_fee"
    assert event["payload"]["reason"] == "below_min_sample_size"
    assert event["payload"]["sample_size"] == 1


async def test_fanout_isolates_failing_fields() -> None:
    step = _make_step(
        [
            _proactive_field("fails", filter_keys=["corridor"]),
            _proactive_field("succeeds", filter_keys=["corridor"]),
        ]
    )
    contract = _make_contract([step])
    priceframe = _StubPriceFrame(
        {
            "fails": RuntimeError("boom"),
            "succeeds": {
                "value": 2.0,
                "unit": "PCT_AMOUNT",
                "sample_size": 8,
                "range": {"min": 1.0, "max": 3.0, "p25": 1.5, "p75": 2.5},
                "basis": {"aggregation": "median", "filter_keys": ["corridor"]},
                "context_used": {"corridor": "USA-IND"},
                "as_of": "2026-05-24T00:00:00Z",
            },
        }
    )

    events = await fan_out_historical_suggestions(
        contract=contract,
        step=step,
        draft_state={"summary": {"corridor": "USA-IND"}},
        auth_ctx=_AUTH,
        priceframe=priceframe,  # type: ignore[arg-type]
    )

    by_field = {event["payload"]["field_id"]: event for event in events}
    assert by_field["fails"]["event_type"] == EVENT_NO_SIGNAL
    assert by_field["fails"]["payload"]["reason"] == "error"
    assert by_field["succeeds"]["event_type"] == EVENT_READY
    assert by_field["succeeds"]["payload"]["value"] == 2.0


async def test_fanout_filter_keys_isolate_context() -> None:
    """build_suggestion_ctx is per-field, not per-step."""

    step = _make_step(
        [
            _proactive_field("a", filter_keys=["corridor"]),
            _proactive_field("b", filter_keys=["corridor", "customer_segment"]),
        ]
    )
    contract = _make_contract([step])
    captured_ctx: dict[str, dict[str, Any]] = {}

    class _CaptureClient:
        async def get_json(
            self,
            path: str,  # noqa: ARG002
            *,
            jwt_raw: str,  # noqa: ARG002
            params: Mapping[str, Any] | None = None,
        ) -> dict[str, Any]:
            import base64
            import json as _json

            assert params is not None
            field = params["field"]
            ctx = _json.loads(base64.b64decode(params["ctx"]).decode("utf-8"))
            captured_ctx[field] = ctx
            return {
                "value": 1,
                "unit": "BPS",
                "sample_size": 5,
                "range": None,
                "basis": {},
                "context_used": ctx,
                "as_of": "2026-05-24T00:00:00Z",
            }

    await fan_out_historical_suggestions(
        contract=contract,
        step=step,
        draft_state={
            "summary": {
                "corridor": "USA-IND",
                "customer_segment": "RETAIL",
                "service": "C2C",
            }
        },
        auth_ctx=_AUTH,
        priceframe=_CaptureClient(),  # type: ignore[arg-type]
    )

    assert captured_ctx["a"] == {"corridor": "USA-IND"}
    assert captured_ctx["b"] == {"corridor": "USA-IND", "customer_segment": "RETAIL"}


async def test_emit_historical_suggestions_skips_without_permission(
    session_factory: tuple[AsyncEngine, Any, str],
) -> None:
    _engine, factory, run_id = session_factory
    no_perm_auth = AuthContext(
        user_id=1,
        role_code="ROLE_AM_SALES",
        profile_code="PROFILE_SALES",
        permissions=("agent.enabled",),
        jwt_raw="test-jwt",
        session_id=1,
    )
    step = _make_step([_proactive_field("default_transaction_fee")])
    contract = _make_contract([step])
    priceframe = _StubPriceFrame({"default_transaction_fee": {"value": 1}})

    async with factory() as session:
        events = await emit_historical_suggestions(
            session,
            run_id=run_id,
            contract=contract,
            step=step,
            draft_state={"summary": {"corridor": "USA-IND"}},
            auth_ctx=no_perm_auth,
            priceframe=priceframe,  # type: ignore[arg-type]
        )
        await session.commit()
    assert events == []
    assert priceframe.calls == []

    async with factory() as session:
        stored = await list_run_events(session, run_id=run_id)
    assert stored == []


async def test_emit_historical_suggestions_persists_events(
    session_factory: tuple[AsyncEngine, Any, str],
) -> None:
    _engine, factory, run_id = session_factory
    step = _make_step([_proactive_field("default_transaction_fee", filter_keys=["corridor"])])
    contract = _make_contract([step])
    priceframe = _StubPriceFrame(
        {
            "default_transaction_fee": {
                "value": 1.5,
                "unit": "PCT_AMOUNT",
                "sample_size": 9,
                "range": None,
                "basis": {},
                "context_used": {"corridor": "USA-IND"},
                "as_of": "2026-05-24T00:00:00Z",
            }
        }
    )

    async with factory() as session:
        emitted = await emit_historical_suggestions(
            session,
            run_id=run_id,
            contract=contract,
            step=step,
            draft_state={"summary": {"corridor": "USA-IND"}},
            auth_ctx=_AUTH,
            priceframe=priceframe,  # type: ignore[arg-type]
        )
        await session.commit()
    assert len(emitted) == 1
    assert emitted[0]["event_type"] == EVENT_READY

    async with factory() as session:
        stored = await list_run_events(session, run_id=run_id)
    types = [event.event_type for event in stored]
    assert types == [EVENT_READY]
    payload = stored[0].payload
    assert payload["field_id"] == "default_transaction_fee"
    assert payload["value"] == 1.5


# ---------------------------------------------------------------------------
# Wave C — blending tests (deterministic rules + Flash fallback)
# ---------------------------------------------------------------------------


def _historical(value: float, sample_size: int) -> dict[str, Any]:
    return {
        "value": value,
        "unit": "PCT_AMOUNT",
        "sample_size": sample_size,
        "range": {"min": value * 0.8, "max": value * 1.2},
        "basis": {"aggregation": "median"},
        "as_of": "2026-05-25T00:00:00Z",
    }


def _market(value: float | None, confidence: float) -> dict[str, Any]:
    return {
        "value": value,
        "confidence": confidence,
        "summary": "market summary",
        "citations": [{"url": "https://x", "title": "x", "snippet": "x"}],
        "cache_hit": False,
    }


def test_blend_strong_historical_and_market_uses_seventy_thirty() -> None:
    result = blend(_historical(1.0, 20), _market(2.0, 0.8))
    assert not result.no_signal
    # 70/30 weighted = 0.7*1.0 + 0.3*2.0 = 1.3
    # BUT disagreement 100% (>25%), so deterministic 70/30 is used with the
    # "Flash fallback unavailable" suffix since no reconciler is wired.
    assert result.value == pytest.approx(1.3, rel=1e-6)
    assert result.sources_used == ["historical", "market"]
    assert "70/30" in result.rationale


def test_blend_strong_historical_no_disagreement_clean_rationale() -> None:
    # Within 25% — clean rationale, no fallback suffix.
    result = blend(_historical(1.0, 20), _market(1.1, 0.8))
    assert result.value == pytest.approx(0.7 * 1.0 + 0.3 * 1.1, rel=1e-6)
    assert "Flash fallback unavailable" not in result.rationale
    assert result.sources_used == ["historical", "market"]


def test_blend_moderate_historical_and_market_50_50() -> None:
    result = blend(_historical(1.0, 5), _market(1.2, 0.6))
    # 50/50 = 1.1
    assert result.value == pytest.approx(1.1, rel=1e-6)
    assert "50/50" in result.rationale
    assert result.sources_used == ["historical", "market"]


def test_blend_market_only_when_historical_weak() -> None:
    # sample size below 3, market confidence >= 0.5
    result = blend(_historical(0.5, 1), _market(1.4, 0.6))
    assert result.value == 1.4
    assert result.sources_used == ["market"]
    assert "market only" in result.rationale


def test_blend_no_signal_when_both_weak() -> None:
    result = blend(_historical(0.5, 1), _market(0.7, 0.2))
    assert result.no_signal is True
    assert result.value is None
    assert result.reason == "confidence_below_threshold"


def test_blend_historical_only_when_market_missing() -> None:
    result = blend(_historical(1.2, 8), None)
    assert result.value == 1.2
    assert result.sources_used == ["historical"]


async def test_blend_calls_flash_on_disagreement_over_25_percent() -> None:
    """Strong + strong with >25% disagreement triggers the Flash reconciler."""

    reconciler = AsyncMock(spec=FlashReconciler)
    reconciler.reconcile.return_value = {
        "recommended": 1.75,
        "rationale": "weighted toward market because corridor is new",
    }

    result = await blend_async(
        _historical(1.0, 20),
        _market(2.0, 0.85),
        flash_reconciler=reconciler,
    )
    assert result.value == 1.75
    assert result.sources_used == ["historical", "market", "flash"]
    assert "corridor is new" in result.rationale
    reconciler.reconcile.assert_awaited_once()


async def test_blend_async_does_not_call_flash_within_threshold() -> None:
    reconciler = AsyncMock(spec=FlashReconciler)
    reconciler.reconcile.return_value = {"recommended": 99.0, "rationale": "ignored"}

    # Disagreement is ~10% — below the 25% threshold.
    result = await blend_async(
        _historical(1.0, 20),
        _market(1.1, 0.85),
        flash_reconciler=reconciler,
    )
    assert result.value == pytest.approx(0.7 + 0.33, rel=1e-3)
    reconciler.reconcile.assert_not_called()


async def test_blend_async_flash_failure_falls_back_to_default_weighted() -> None:
    """When Flash raises or returns nothing, the deterministic blend is used."""

    reconciler = AsyncMock(spec=FlashReconciler)
    reconciler.reconcile.side_effect = RuntimeError("flash down")

    result = await blend_async(
        _historical(1.0, 20),
        _market(2.0, 0.85),
        flash_reconciler=reconciler,
    )
    # Falls back to 70/30 deterministic.
    assert result.value == pytest.approx(1.3, rel=1e-6)
    assert result.sources_used == ["historical", "market"]


# ---------------------------------------------------------------------------
# Wave C — fan_out_suggestions integration (historical + market)
# ---------------------------------------------------------------------------


def _market_field(
    field_id: str,
    *,
    template: str = "median transaction fee for {corridor}",
    max_age_seconds: int = 600,
    confidence_threshold: float = 0.5,
) -> dict[str, Any]:
    return {
        "id": field_id,
        "label": field_id,
        "type": "currency",
        "required": False,
        "suggestion": {
            "mode": "proactive",
            "sources": ["historical", "market"],
            "historical": {
                "aggregation": "median",
                "filter_keys": ["corridor"],
                "min_sample_size": 3,
            },
            "market": {
                "research_query_template": template,
                "max_age_seconds": max_age_seconds,
            },
            "confidence_threshold": confidence_threshold,
        },
    }


class _StubGroundingClient:
    """Drop-in for ``GeminiGroundingClient`` returning canned responses."""

    configured = True

    def __init__(self, responses: Mapping[str, dict[str, Any]]) -> None:
        self._responses = responses
        self.calls: list[str] = []

    async def research(self, query: str, *, timeout_seconds: float) -> dict[str, Any]:  # noqa: ARG002
        self.calls.append(query)
        for key, response in self._responses.items():
            if key in query:
                return dict(response)
        return {
            "value": None,
            "confidence": 0.0,
            "summary": "research unavailable",
            "citations": [],
        }


def _settings_for_blend() -> Settings:
    return Settings(
        gemini_api_key="fake-key",
        web_research_timeout_seconds=1.0,
        web_research_default_max_age_seconds=600,
        web_research_estimated_cost_usd=0.01,
        max_research_calls_per_run=5,
        max_research_cost_per_run_usd=0.05,
        web_research_disagreement_threshold=0.25,
    )


async def test_fan_out_suggestions_emits_blended_payload() -> None:
    field = _market_field("default_transaction_fee")
    step = _make_step([field])
    contract = _make_contract([step])
    historical_pf = _StubPriceFrame(
        {
            "default_transaction_fee": {
                "value": 1.0,
                "unit": "PCT_AMOUNT",
                "sample_size": 12,
                "range": {"min": 0.5, "max": 1.5},
                "basis": {"aggregation": "median"},
                "context_used": {"corridor": "USA-IND"},
                "as_of": "2026-05-25T00:00:00Z",
            }
        }
    )
    grounding = _StubGroundingClient(
        {
            "USA-IND": {
                "value": 1.1,
                "confidence": 0.8,
                "summary": "Market median around 1.1%",
                "citations": [{"url": "https://example.com", "title": "Source", "snippet": "snip"}],
            }
        }
    )

    events = await fan_out_suggestions(
        contract=contract,
        step=step,
        draft_state={"summary": {"corridor": "USA-IND"}},
        auth_ctx=_AUTH,
        priceframe=historical_pf,  # type: ignore[arg-type]
        settings=_settings_for_blend(),
        budget=RunBudget(),
        cache=ResearchCache(),
        grounding_client=grounding,  # type: ignore[arg-type]
        flash_reconciler=None,
    )

    assert len(events) == 1
    event = events[0]
    assert event["event_type"] == EVENT_READY
    payload = event["payload"]
    assert payload["field_id"] == "default_transaction_fee"
    assert payload["historical"]["value"] == 1.0
    assert payload["market"]["value"] == 1.1
    assert payload["market"]["citations"][0]["url"] == "https://example.com"
    assert payload["proposed"]["sources_used"] == ["historical", "market"]
    assert payload["proposed"]["value"] == pytest.approx(0.7 * 1.0 + 0.3 * 1.1, rel=1e-6)
    assert "70/30" in payload["proposed"]["rationale"]


async def test_fan_out_suggestions_emits_no_signal_when_both_bands_weak() -> None:
    field = _market_field("default_transaction_fee", confidence_threshold=0.5)
    step = _make_step([field])
    contract = _make_contract([step])
    historical_pf = _StubPriceFrame(
        {
            "default_transaction_fee": {
                "value": None,
                "unit": None,
                "sample_size": 1,
                "no_signal": True,
            }
        }
    )
    grounding = _StubGroundingClient(
        {
            "USA-IND": {
                "value": None,
                "confidence": 0.0,
                "summary": "no data",
                "citations": [],
            }
        }
    )

    events = await fan_out_suggestions(
        contract=contract,
        step=step,
        draft_state={"summary": {"corridor": "USA-IND"}},
        auth_ctx=_AUTH,
        priceframe=historical_pf,  # type: ignore[arg-type]
        settings=_settings_for_blend(),
        budget=RunBudget(),
        cache=ResearchCache(),
        grounding_client=grounding,  # type: ignore[arg-type]
        flash_reconciler=None,
    )
    assert len(events) == 1
    assert events[0]["event_type"] == EVENT_NO_SIGNAL
    assert events[0]["payload"]["reason"] == "confidence_below_threshold"


async def test_fan_out_suggestions_budget_exhaustion_keeps_historical_only() -> None:
    field = _market_field("default_transaction_fee")
    step = _make_step([field])
    contract = _make_contract([step])
    historical_pf = _StubPriceFrame(
        {
            "default_transaction_fee": {
                "value": 1.0,
                "unit": "PCT_AMOUNT",
                "sample_size": 12,
                "context_used": {"corridor": "USA-IND"},
                "as_of": "2026-05-25T00:00:00Z",
            }
        }
    )
    grounding = _StubGroundingClient(
        {"USA-IND": {"value": 99.0, "confidence": 0.99, "summary": "x", "citations": []}}
    )
    budget = RunBudget(max_calls=0, max_cost_usd=0.05)  # immediately exhausted

    events = await fan_out_suggestions(
        contract=contract,
        step=step,
        draft_state={"summary": {"corridor": "USA-IND"}},
        auth_ctx=_AUTH,
        priceframe=historical_pf,  # type: ignore[arg-type]
        settings=_settings_for_blend(),
        budget=budget,
        cache=ResearchCache(),
        grounding_client=grounding,  # type: ignore[arg-type]
        flash_reconciler=None,
    )
    assert len(events) == 1
    payload = events[0]["payload"]
    assert payload["historical"]["value"] == 1.0
    # market band defaults to None (empty payload), so proposed comes from
    # historical only.
    assert payload["market"] is None or payload["market"].get("value") is None
    assert payload["proposed"]["sources_used"] == ["historical"]
    assert grounding.calls == []  # budget refused the call


async def test_fan_out_suggestions_market_only_field_emits_blended_event() -> None:
    """A field with only ``market`` in sources still produces a blended event."""

    field = {
        "id": "interbank_corridor_rate",
        "label": "Interbank corridor rate",
        "type": "currency",
        "required": False,
        "suggestion": {
            "mode": "proactive",
            "sources": ["market"],
            "market": {
                "research_query_template": "interbank rate for {corridor}",
                "max_age_seconds": 600,
            },
            "confidence_threshold": 0.5,
        },
    }
    step = _make_step([field])
    contract = _make_contract([step])
    historical_pf = _StubPriceFrame({})
    grounding = _StubGroundingClient(
        {
            "USA-IND": {
                "value": 83.4,
                "confidence": 0.7,
                "summary": "Interbank ~83.4 INR/USD",
                "citations": [{"url": "https://rate", "title": "Rate", "snippet": "x"}],
            }
        }
    )

    events = await fan_out_suggestions(
        contract=contract,
        step=step,
        draft_state={"summary": {"corridor": "USA-IND"}},
        auth_ctx=_AUTH,
        priceframe=historical_pf,  # type: ignore[arg-type]
        settings=_settings_for_blend(),
        budget=RunBudget(),
        cache=ResearchCache(),
        grounding_client=grounding,  # type: ignore[arg-type]
        flash_reconciler=None,
    )
    assert len(events) == 1
    payload = events[0]["payload"]
    assert payload["proposed"]["sources_used"] == ["market"]
    assert payload["proposed"]["value"] == 83.4


def test_blend_result_serialization() -> None:
    """``BlendResult.to_payload`` returns the proposed sub-payload shape."""

    result = BlendResult(
        no_signal=False,
        value=1.5,
        rationale="r",
        sources_used=["historical", "market"],
    )
    assert result.to_payload() == {
        "value": 1.5,
        "rationale": "r",
        "sources_used": ["historical", "market"],
    }


async def test_fan_out_suggestions_threads_parent_span_into_fanout_span(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``parent_span`` must be forwarded into ``suggestion_fanout_span`` so
    the Langfuse trace properly nests the fan-out under the workflow step
    span (Phase 11 / M2 open item).
    """

    captured_parents: list[Any] = []

    @contextmanager
    def _recording_fanout_span(parent: Any, *, step_id: str, field_count: int | None = None) -> Any:
        del step_id, field_count
        captured_parents.append(parent)
        yield object()

    monkeypatch.setattr(
        "xframe_agent.agent.suggestions.suggestion_fanout_span", _recording_fanout_span
    )

    step = _make_step([_proactive_field("default_transaction_fee", filter_keys=["corridor"])])
    contract = _make_contract([step])
    priceframe = _StubPriceFrame(
        {
            "default_transaction_fee": {
                "value": 1.0,
                "unit": "PCT_AMOUNT",
                "sample_size": 5,
                "range": {"min": 0.5, "max": 1.5},
                "basis": {"aggregation": "median"},
                "context_used": {"corridor": "USA-IND"},
                "as_of": "2026-05-25T00:00:00Z",
            }
        }
    )

    sentinel_parent = object()
    await fan_out_suggestions(
        contract=contract,
        step=step,
        draft_state={"summary": {"corridor": "USA-IND"}},
        auth_ctx=_AUTH,
        priceframe=priceframe,  # type: ignore[arg-type]
        parent_span=sentinel_parent,
    )

    assert captured_parents == [sentinel_parent]
