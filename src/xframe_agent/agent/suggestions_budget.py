"""Per-run budget tracking for the market suggestion engine (M2 Phase 9).

The market band is powered by ``WebResearchTool``, which wraps Gemini grounding
and is expensive enough to warrant its own ceiling — separate from the
``LoopBudget`` that polices the agent's deterministic/model loop.

A :class:`RunBudget` instance is attached to a single workflow run and shared
across the per-field fan-out so the **5 calls / $0.05** caps (whichever hits
first, per spec §5.2) apply across every market field consulted during one
step entry. When either cap is exhausted the tool returns a graceful empty
payload (``value=None``, ``confidence=0``) instead of raising, so the wizard
falls back to historical-only seamlessly.

The budget intentionally exposes a tiny API (``can_spend`` / ``record``) so it
can be unit-tested without any of the surrounding orchestration. Cost is
recorded as a *post-call* fact — callers ask ``can_spend`` with the
**estimated** cost before issuing the network call.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class RunBudget:
    """Cap on web-research calls + estimated USD spend for one workflow run.

    Both caps are advisory in the sense that the *consumer* must ask
    :meth:`can_spend` before doing the work; :meth:`record` only updates the
    counters after a successful spend so failed/timed-out calls don't burn
    budget that should be available for retries on other fields.
    """

    max_calls: int = 5
    max_cost_usd: float = 0.05
    calls: int = 0
    cost_usd: float = 0.0

    def can_spend(self, estimated_cost_usd: float) -> bool:
        """Return ``True`` when a new call fits under both caps.

        The call-count check uses ``<`` so the Nth call is allowed but the
        N+1th is not. The cost check uses ``<=`` so the *exact* ceiling is
        allowed (float math means an ``estimated_cost_usd`` of 0.01 paired
        with a $0.05 cap can land exactly on the boundary).
        """

        if estimated_cost_usd < 0:
            raise ValueError("estimated_cost_usd must be non-negative")
        if self.calls >= self.max_calls:
            return False
        return self.cost_usd + estimated_cost_usd <= self.max_cost_usd

    def record(self, cost_usd: float) -> None:
        """Mark one call as spent with its realised USD cost."""

        if cost_usd < 0:
            raise ValueError("cost_usd must be non-negative")
        self.calls += 1
        self.cost_usd += cost_usd

    @property
    def exhausted(self) -> bool:
        """Both caps fully consumed — no further spend is possible."""

        return self.calls >= self.max_calls or self.cost_usd >= self.max_cost_usd

    def snapshot(self) -> dict[str, float | int]:
        return {
            "calls": self.calls,
            "max_calls": self.max_calls,
            "cost_usd": round(self.cost_usd, 6),
            "max_cost_usd": self.max_cost_usd,
        }


__all__ = ["RunBudget"]
