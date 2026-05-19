"""LLM-as-judge hooks for future free-text evals."""

from __future__ import annotations


def judge_free_text(*, expected: str, actual: str) -> bool:
    """Deterministic placeholder used until judge-model credentials are configured."""

    return expected.strip() == actual.strip()
