"""Unit tests for the M2.1.B step proposal orchestrator."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from xframe_agent.agent.step_proposals import (
    SOURCE_DEFAULT,
    SOURCE_DRAFT,
    SOURCE_HISTORICAL,
    _confidence_score,
    propose_step_payload,
)
from xframe_agent.agent.suggestions_budget import RunBudget
from xframe_agent.auth.jwt import AuthContext

_AUTH_WITH_SUGGESTIONS = AuthContext(
    user_id=1,
    role_code="ROLE_AM_SALES",
    profile_code="PROFILE_SALES",
    permissions=("agent.enabled", "agent.suggestions.read"),
    jwt_raw="test-jwt",
    session_id=1,
)


def _historical_field(
    field_id: str,
    *,
    filter_keys: list[str] | None = None,
    field_type: str = "currency",
) -> dict[str, Any]:
    return {
        "id": field_id,
        "label": field_id,
        "type": field_type,
        "required": True,
        "suggestion": {
            "mode": "proactive",
            "sources": ["historical"],
            "historical": {
                "aggregation": "median",
                "filter_keys": filter_keys or ["corridor"],
                "min_sample_size": 3,
            },
        },
    }


def _enum_field(field_id: str, options: list[str]) -> dict[str, Any]:
    return {
        "id": field_id,
        "label": field_id,
        "type": "enum",
        "required": True,
        "enum_options": [{"value": v, "label": v} for v in options],
    }


def _string_field(field_id: str) -> dict[str, Any]:
    return {
        "id": field_id,
        "label": field_id,
        "type": "string",
        "required": True,
    }


def _make_step(
    fields: list[dict[str, Any]],
    *,
    step_id: str = "pricing",
    essential_field_ids: list[str] | None = None,
    approval_mode: str = "per_tab",
) -> dict[str, Any]:
    step: dict[str, Any] = {
        "id": step_id,
        "title": step_id.title(),
        "description": "test",
        "approval_mode": approval_mode,
        "fields": fields,
    }
    if essential_field_ids is not None:
        step["essential_field_ids"] = essential_field_ids
    return step


def _make_contract(steps: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": "create_pricing_request",
        "version": "v1",
        "title": "Create pricing request",
        "steps": steps,
    }


class _StubPriceFrame:
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


# ---------------------------------------------------------------------------
# propose_step_payload behaviour
# ---------------------------------------------------------------------------


async def test_propose_step_payload_returns_none_when_no_essentials() -> None:
    """A step with no ``essential_field_ids`` produces no proposal."""

    step = _make_step([_string_field("title")])
    contract = _make_contract([step])
    pf = _StubPriceFrame({})

    result = await propose_step_payload(
        contract=contract,
        step=step,
        draft_state={},
        auth_ctx=_AUTH_WITH_SUGGESTIONS,
        priceframe=pf,  # type: ignore[arg-type]
        budget=RunBudget(),
    )
    assert result is None
    assert pf.calls == []


async def test_propose_step_payload_returns_none_when_essentials_unresolvable() -> None:
    """When every essential is a free-form string with no draft + no history,
    the orchestrator can't safely fill anything and returns None."""

    step = _make_step(
        [_string_field("notes"), _string_field("comment")],
        essential_field_ids=["notes", "comment"],
    )
    contract = _make_contract([step])
    pf = _StubPriceFrame({})

    result = await propose_step_payload(
        contract=contract,
        step=step,
        draft_state={},
        auth_ctx=_AUTH_WITH_SUGGESTIONS,
        priceframe=pf,  # type: ignore[arg-type]
        budget=RunBudget(),
    )
    assert result is None


async def test_propose_step_payload_uses_existing_draft_values() -> None:
    """Fields already filled in the draft are reused untouched, with source 'draft'."""

    step = _make_step(
        [
            _historical_field("default_transaction_fee"),
            _string_field("name"),
        ],
        step_id="summary",
        essential_field_ids=["default_transaction_fee", "name"],
    )
    contract = _make_contract([step])
    pf = _StubPriceFrame({})  # endpoint never called because draft wins

    draft_state = {
        "summary": {
            "default_transaction_fee": 1.42,
            "name": "Acme",
        }
    }

    result = await propose_step_payload(
        contract=contract,
        step=step,
        draft_state=draft_state,
        auth_ctx=_AUTH_WITH_SUGGESTIONS,
        priceframe=pf,  # type: ignore[arg-type]
        budget=RunBudget(),
    )
    assert result is not None
    assert result["step_id"] == "summary"
    proposal = result["proposal"]
    assert proposal["payload"] == {"default_transaction_fee": 1.42, "name": "Acme"}
    assert proposal["sources_used"] == [SOURCE_DRAFT]
    assert proposal["confidence"] > 0
    assert pf.calls == []  # short-circuited on draft


async def test_propose_step_payload_calls_historical_for_unfilled_essentials() -> None:
    """Essentials without a draft value get pulled from the historical endpoint."""

    step = _make_step(
        [_historical_field("default_transaction_fee", filter_keys=["corridor"])],
        essential_field_ids=["default_transaction_fee"],
    )
    contract = _make_contract([step])
    pf = _StubPriceFrame(
        {
            "default_transaction_fee": {
                "value": 1.5,
                "unit": "PCT_AMOUNT",
                "sample_size": 12,
                "range": {"min": 0.5, "max": 3.0, "p25": 1.0, "p75": 2.0},
                "basis": {"aggregation": "median", "filter_keys": ["corridor"]},
                "context_used": {"corridor": "USA-IND"},
                "as_of": "2026-05-24T00:00:00Z",
            }
        }
    )

    result = await propose_step_payload(
        contract=contract,
        step=step,
        draft_state={"summary": {"corridor": "USA-IND"}},
        auth_ctx=_AUTH_WITH_SUGGESTIONS,
        priceframe=pf,  # type: ignore[arg-type]
        budget=RunBudget(),
    )
    assert result is not None
    proposal = result["proposal"]
    assert proposal["payload"] == {"default_transaction_fee": 1.5}
    assert proposal["sources_used"] == [SOURCE_HISTORICAL]
    # The historical endpoint was hit exactly once.
    assert len(pf.calls) == 1
    assert pf.calls[0][1]["field"] == "default_transaction_fee"  # type: ignore[index]


async def test_propose_step_payload_falls_back_to_defaults_when_no_history() -> None:
    """When historical signals are absent, sensible enum/numeric defaults fill the gap."""

    step = _make_step(
        [
            _enum_field("payment_schedule", ["100% on signature", "50/50", "Custom"]),
            _historical_field("standard_commitment_fee"),
        ],
        essential_field_ids=["payment_schedule", "standard_commitment_fee"],
    )
    contract = _make_contract([step])
    # historical returns no_signal => fall back to default (which for currency
    # is the validation.min or 0).
    pf = _StubPriceFrame(
        {
            "standard_commitment_fee": {
                "value": None,
                "sample_size": 1,
                "no_signal": True,
            }
        }
    )

    result = await propose_step_payload(
        contract=contract,
        step=step,
        draft_state={},
        auth_ctx=_AUTH_WITH_SUGGESTIONS,
        priceframe=pf,  # type: ignore[arg-type]
        budget=RunBudget(),
    )
    assert result is not None
    proposal = result["proposal"]
    # Enum picked the first option, currency defaulted to 0.
    assert proposal["payload"]["payment_schedule"] == "100% on signature"
    assert proposal["payload"]["standard_commitment_fee"] == 0
    assert set(proposal["sources_used"]) == {SOURCE_DEFAULT}


async def test_propose_step_payload_respects_budget() -> None:
    """An already-exhausted budget skips the historical fetch and falls back to defaults."""

    step = _make_step(
        [_historical_field("standard_commitment_fee")],
        essential_field_ids=["standard_commitment_fee"],
    )
    contract = _make_contract([step])
    pf = _StubPriceFrame(
        {
            "standard_commitment_fee": {
                "value": 99.0,
                "sample_size": 5,
            }
        }
    )

    # Exhausted from the start — can_spend always returns False.
    exhausted = RunBudget(max_calls=0, max_cost_usd=0.0)

    result = await propose_step_payload(
        contract=contract,
        step=step,
        draft_state={},
        auth_ctx=_AUTH_WITH_SUGGESTIONS,
        priceframe=pf,  # type: ignore[arg-type]
        budget=exhausted,
    )
    assert result is not None
    proposal = result["proposal"]
    # No historical call happened, so the currency default (0) is used.
    assert proposal["payload"] == {"standard_commitment_fee": 0}
    assert proposal["sources_used"] == [SOURCE_DEFAULT]
    assert pf.calls == []


async def test_propose_step_payload_isolates_per_field_failures() -> None:
    """One failing historical call must not poison the rest of the step."""

    step = _make_step(
        [
            _historical_field("breaks_field"),
            _historical_field("works_field"),
        ],
        essential_field_ids=["breaks_field", "works_field"],
    )
    contract = _make_contract([step])
    pf = _StubPriceFrame(
        {
            "breaks_field": RuntimeError("backend down"),
            "works_field": {
                "value": 2.5,
                "sample_size": 8,
            },
        }
    )

    result = await propose_step_payload(
        contract=contract,
        step=step,
        draft_state={},
        auth_ctx=_AUTH_WITH_SUGGESTIONS,
        priceframe=pf,  # type: ignore[arg-type]
        budget=RunBudget(),
    )
    assert result is not None
    proposal = result["proposal"]
    # The failing field falls through to the currency default (0); the working
    # field returns 2.5 from history. Both essentials end up populated.
    assert proposal["payload"] == {"breaks_field": 0, "works_field": 2.5}
    assert set(proposal["sources_used"]) == {SOURCE_DEFAULT, SOURCE_HISTORICAL}


async def test_propose_step_payload_skips_historical_without_permission() -> None:
    """Without ``agent.suggestions.read`` we never hit the historical endpoint."""

    step = _make_step(
        [_historical_field("standard_commitment_fee")],
        essential_field_ids=["standard_commitment_fee"],
    )
    contract = _make_contract([step])
    pf = _StubPriceFrame({"standard_commitment_fee": {"value": 99.0, "sample_size": 5}})

    no_perm_auth = AuthContext(
        user_id=1,
        role_code="ROLE_AM_SALES",
        profile_code="PROFILE_SALES",
        permissions=("agent.enabled",),  # no suggestions.read
        jwt_raw="test-jwt",
        session_id=1,
    )

    result = await propose_step_payload(
        contract=contract,
        step=step,
        draft_state={},
        auth_ctx=no_perm_auth,
        priceframe=pf,  # type: ignore[arg-type]
        budget=RunBudget(),
    )
    assert result is not None
    assert result["proposal"]["payload"] == {"standard_commitment_fee": 0}
    assert result["proposal"]["sources_used"] == [SOURCE_DEFAULT]
    assert pf.calls == []  # endpoint never touched


async def test_propose_step_payload_ignores_unknown_essential_ids() -> None:
    """An essential id that doesn't match any field on the step is skipped (not an error)."""

    step = _make_step(
        [_enum_field("flavor", ["sweet", "sour"])],
        essential_field_ids=["flavor", "phantom_field"],
    )
    contract = _make_contract([step])

    result = await propose_step_payload(
        contract=contract,
        step=step,
        draft_state={},
        auth_ctx=_AUTH_WITH_SUGGESTIONS,
        priceframe=None,  # no historical lookups at all
        budget=RunBudget(),
    )
    assert result is not None
    assert result["proposal"]["payload"] == {"flavor": "sweet"}


# ---------------------------------------------------------------------------
# confidence
# ---------------------------------------------------------------------------


def test_confidence_score_higher_when_all_essentials_filled() -> None:
    """Full coverage scores higher than partial coverage at the same source mix."""

    full = _confidence_score(
        filled_count=5,
        essential_count=5,
        sources_used={SOURCE_HISTORICAL},
    )
    partial = _confidence_score(
        filled_count=2,
        essential_count=5,
        sources_used={SOURCE_HISTORICAL},
    )
    assert full > partial
    assert 0.0 <= partial <= 1.0
    assert 0.0 <= full <= 1.0


def test_confidence_score_historical_beats_default_at_same_coverage() -> None:
    historical = _confidence_score(
        filled_count=3,
        essential_count=3,
        sources_used={SOURCE_HISTORICAL},
    )
    defaulted = _confidence_score(
        filled_count=3,
        essential_count=3,
        sources_used={SOURCE_DEFAULT},
    )
    assert historical > defaulted


def test_confidence_score_zero_when_no_essentials() -> None:
    assert _confidence_score(0, 0, set()) == pytest.approx(0.0)
