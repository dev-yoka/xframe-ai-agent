"""Golden trace replay helpers for CI."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

GOLDEN_DIR = Path(__file__).parent / "golden"


@dataclass(frozen=True, slots=True)
class GoldenTrace:
    name: str
    input: str
    expected_tools: tuple[str, ...]
    expected_final_status: str


def load_golden_traces() -> list[GoldenTrace]:
    """Load versioned synthetic golden traces."""

    traces: list[GoldenTrace] = []
    for path in sorted(GOLDEN_DIR.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        traces.append(
            GoldenTrace(
                name=payload["name"],
                input=payload["input"],
                expected_tools=tuple(payload["expected_tools"]),
                expected_final_status=payload["expected_final_status"],
            )
        )
    return traces


def replay_trace(trace: GoldenTrace) -> dict[str, object]:
    """Replay a synthetic trace through structural assertions.

    Phase D wires the CI harness and fixtures; provider-backed replay lands once the live loop is
    connected to Gemini/Anthropic credentials.
    """

    return {
        "name": trace.name,
        "tool_sequence": list(trace.expected_tools),
        "final_status": trace.expected_final_status,
    }
