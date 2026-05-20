"""Per-run budget tracking and ceiling enforcement.

Settings ceilings (`max_steps_per_run`, `max_wall_clock_per_run_s`,
`max_tool_calls_per_run`, `max_input_tokens_per_run`, `max_output_tokens_per_run`,
`cost_soft_per_run_usd`, `cost_hard_per_run_usd`) are policed here.

Raise :class:`BudgetExceededError` from a loop step to abort cleanly. Cost is computed
from token usage via :data:`MODEL_COST_TABLE` (USD per 1k tokens, input/output).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter

from xframe_agent.settings import Settings


class BudgetExceededError(Exception):
    """Raised when any per-run budget ceiling has been crossed."""

    def __init__(self, cause: str, message: str) -> None:
        super().__init__(message)
        self.cause = cause


# USD per 1k tokens. Update when provider pricing changes.
MODEL_COST_TABLE: dict[str, tuple[float, float]] = {
    "gemini-2.5-flash": (0.0001, 0.0004),
    "gemini-1.5-flash": (0.000075, 0.0003),
    "claude-haiku-4-5": (0.0008, 0.004),
    "claude-3-5-haiku": (0.0008, 0.004),
}
DEFAULT_COST = (0.0005, 0.002)


@dataclass(slots=True)
class LoopBudget:
    """Mutable counters for one run."""

    settings: Settings
    started_at: float = field(default_factory=perf_counter)
    steps: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    def begin_step(self) -> None:
        if self.steps >= self.settings.max_steps_per_run:
            raise BudgetExceededError(
                "step_budget_exceeded",
                f"step ceiling {self.settings.max_steps_per_run} reached",
            )
        if self.elapsed_seconds() >= self.settings.max_wall_clock_per_run_s:
            raise BudgetExceededError(
                "wall_clock_budget_exceeded",
                f"wall-clock ceiling {self.settings.max_wall_clock_per_run_s}s reached",
            )
        self.steps += 1

    def record_tool_call(self) -> None:
        if self.tool_calls >= self.settings.max_tool_calls_per_run:
            raise BudgetExceededError(
                "tool_call_budget_exceeded",
                f"tool-call ceiling {self.settings.max_tool_calls_per_run} reached",
            )
        self.tool_calls += 1

    def record_usage(self, *, model: str, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        in_rate, out_rate = MODEL_COST_TABLE.get(model, DEFAULT_COST)
        self.cost_usd += (input_tokens * in_rate + output_tokens * out_rate) / 1000.0

        if self.input_tokens > self.settings.max_input_tokens_per_run:
            raise BudgetExceededError(
                "input_token_budget_exceeded",
                f"input token ceiling {self.settings.max_input_tokens_per_run} reached",
            )
        if self.output_tokens > self.settings.max_output_tokens_per_run:
            raise BudgetExceededError(
                "output_token_budget_exceeded",
                f"output token ceiling {self.settings.max_output_tokens_per_run} reached",
            )
        if self.cost_usd > self.settings.cost_hard_per_run_usd:
            raise BudgetExceededError(
                "cost_budget_exceeded",
                f"cost hard ceiling ${self.settings.cost_hard_per_run_usd:.4f} reached",
            )

    def soft_cost_breached(self) -> bool:
        return self.cost_usd > self.settings.cost_soft_per_run_usd

    def elapsed_seconds(self) -> float:
        return perf_counter() - self.started_at

    def snapshot(self) -> dict[str, float | int]:
        return {
            "steps": self.steps,
            "tool_calls": self.tool_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "elapsed_s": round(self.elapsed_seconds(), 3),
        }
