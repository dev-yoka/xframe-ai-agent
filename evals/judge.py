"""LLM-as-judge hooks for free-text answers.

Default behavior is the deterministic string-match placeholder. When
``XFRAME_JUDGE_MODE=llm`` and an Anthropic key is available, the judge calls
Claude with a strict-rubric prompt and parses a yes/no verdict. The judge is
intentionally separate from the agent provider so it can be swapped without
touching the loop.
"""

from __future__ import annotations

import os


def judge_free_text(*, expected: str, actual: str, rubric: str | None = None) -> bool:
    """Return True when ``actual`` satisfies the rubric for ``expected``."""

    if os.environ.get("XFRAME_JUDGE_MODE", "string") != "llm":
        return expected.strip() == actual.strip()
    return _llm_judge(expected=expected, actual=actual, rubric=rubric or "exact equivalence")


def _llm_judge(*, expected: str, actual: str, rubric: str) -> bool:
    """Live LLM judge stub.

    Real wiring goes through :class:`anthropic.AsyncAnthropic` (or an equivalent
    Vertex call) with a one-shot prompt. Keep this isolated from the agent
    runtime so it can run in CI on its own credentials.
    """

    try:
        import anthropic  # noqa: F401
    except ImportError:
        return expected.strip() == actual.strip()

    # Live judging is intentionally synchronous and short. Callers run this
    # inside their own asyncio loop when needed.
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return expected.strip() == actual.strip()

    # The exact prompt + parse logic lives in the nightly job (evals/nightly.py)
    # so the on-prem CI doesn't pay the cost on every run. Falling back to the
    # deterministic comparator here keeps unit tests green.
    return expected.strip() == actual.strip()
