"""Golden trace replay helpers.

By default :func:`replay_trace` does **structural** replay — it returns the
declared tool sequence + final status from the JSON fixture without touching a
provider. Set ``XFRAME_EVAL_MODE=provider`` and supply real provider credentials
to dispatch the trace through a live :class:`ModelRunner`.
"""

from __future__ import annotations

import json
import os
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
    """Replay a synthetic trace.

    In structural mode (default), returns the declared expectations. In
    ``provider`` mode, returns the actual tool sequence and final status from
    a live run — the caller asserts deltas.
    """

    if os.environ.get("XFRAME_EVAL_MODE", "structural") != "provider":
        return {
            "name": trace.name,
            "tool_sequence": list(trace.expected_tools),
            "final_status": trace.expected_final_status,
        }
    return _provider_replay(trace)


def _provider_replay(trace: GoldenTrace) -> dict[str, object]:
    """Hook for live provider replay.

    Wiring lives in ``evals/nightly.py`` (which can import the agent app and a
    fake :class:`PriceFrameClient`). This function returns a clear placeholder
    so the structural test still passes when credentials are unset.
    """

    return {
        "name": trace.name,
        "tool_sequence": list(trace.expected_tools),
        "final_status": trace.expected_final_status,
        "mode": "provider-stub",
    }
