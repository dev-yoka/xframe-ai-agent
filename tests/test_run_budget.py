"""Tests for the per-run web-research budget (M2 Phase 9 / Wave C)."""

from __future__ import annotations

import pytest

from xframe_agent.agent.suggestions_budget import RunBudget


def test_default_budget_allows_five_calls_under_cost_cap() -> None:
    budget = RunBudget()
    for _ in range(5):
        assert budget.can_spend(0.01)
        budget.record(0.01)
    assert budget.exhausted
    assert not budget.can_spend(0.01)


def test_budget_blocks_after_call_cap() -> None:
    budget = RunBudget(max_calls=2, max_cost_usd=1.0)
    assert budget.can_spend(0.001)
    budget.record(0.001)
    assert budget.can_spend(0.001)
    budget.record(0.001)
    # third call refused by call cap even though cost cap has plenty of room
    assert not budget.can_spend(0.001)
    assert budget.exhausted


def test_budget_blocks_after_cost_cap() -> None:
    budget = RunBudget(max_calls=100, max_cost_usd=0.05)
    # Spend up to $0.05.
    for _ in range(5):
        assert budget.can_spend(0.01)
        budget.record(0.01)
    # cost cap exhausted before call cap; further estimated spend refused.
    assert not budget.can_spend(0.001)
    assert budget.exhausted


def test_budget_refuses_call_that_would_overshoot_cost_cap() -> None:
    budget = RunBudget(max_calls=10, max_cost_usd=0.05)
    budget.record(0.045)
    # Allowed: would land exactly on cap.
    assert budget.can_spend(0.005)
    # Refused: would cross cap by 1c.
    assert not budget.can_spend(0.006)


def test_budget_rejects_negative_estimates() -> None:
    budget = RunBudget()
    with pytest.raises(ValueError):
        budget.can_spend(-0.01)
    with pytest.raises(ValueError):
        budget.record(-0.01)


def test_snapshot_reports_counters() -> None:
    budget = RunBudget(max_calls=3, max_cost_usd=0.04)
    budget.record(0.012)
    snap = budget.snapshot()
    assert snap == {
        "calls": 1,
        "max_calls": 3,
        "cost_usd": 0.012,
        "max_cost_usd": 0.04,
    }
