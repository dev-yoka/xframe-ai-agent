"""Per-run budget ceiling tests."""

from __future__ import annotations

import pytest

from xframe_agent.agent.budget import BudgetExceededError, LoopBudget
from xframe_agent.settings import Settings


def _settings(**overrides: float) -> Settings:
    base: dict[str, float] = {
        "max_steps_per_run": 3,
        "max_tool_calls_per_run": 2,
        "max_input_tokens_per_run": 100,
        "max_output_tokens_per_run": 100,
        "max_wall_clock_per_run_s": 60,
        "cost_soft_per_run_usd": 0.001,
        "cost_hard_per_run_usd": 0.005,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_step_ceiling_aborts() -> None:
    budget = LoopBudget(settings=_settings())
    budget.begin_step()
    budget.begin_step()
    budget.begin_step()
    with pytest.raises(BudgetExceededError) as exc:
        budget.begin_step()
    assert exc.value.cause == "step_budget_exceeded"


def test_tool_call_ceiling_aborts() -> None:
    budget = LoopBudget(settings=_settings())
    budget.record_tool_call()
    budget.record_tool_call()
    with pytest.raises(BudgetExceededError) as exc:
        budget.record_tool_call()
    assert exc.value.cause == "tool_call_budget_exceeded"


def test_input_token_ceiling_aborts() -> None:
    budget = LoopBudget(settings=_settings())
    with pytest.raises(BudgetExceededError) as exc:
        budget.record_usage(model="gemini-2.5-flash", input_tokens=200, output_tokens=0)
    assert exc.value.cause == "input_token_budget_exceeded"


def test_cost_hard_ceiling_aborts() -> None:
    budget = LoopBudget(
        settings=_settings(max_input_tokens_per_run=10_000, max_output_tokens_per_run=10_000)
    )
    # 1000 input + 1000 output on default rate (0.0005, 0.002) per 1k = $0.0025.
    budget.record_usage(model="unknown-model", input_tokens=1000, output_tokens=1000)
    with pytest.raises(BudgetExceededError) as exc:
        # Push past the 0.005 hard ceiling.
        budget.record_usage(model="unknown-model", input_tokens=2000, output_tokens=2000)
    assert exc.value.cause == "cost_budget_exceeded"


def test_soft_cost_flag() -> None:
    budget = LoopBudget(
        settings=_settings(max_input_tokens_per_run=10_000, max_output_tokens_per_run=10_000)
    )
    assert budget.soft_cost_breached() is False
    budget.record_usage(model="unknown-model", input_tokens=1000, output_tokens=1000)
    assert budget.soft_cost_breached() is True
