"""CI entry point for synthetic golden trace checks."""

from __future__ import annotations

from evals.replay import load_golden_traces, replay_trace


def test_all_golden_traces_replay_with_expected_structure() -> None:
    traces = load_golden_traces()

    assert len(traces) == 5
    for trace in traces:
        result = replay_trace(trace)
        assert result["tool_sequence"] == list(trace.expected_tools)
        assert result["final_status"] == trace.expected_final_status
