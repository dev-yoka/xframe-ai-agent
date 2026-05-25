"""CI entry point for synthetic golden trace checks."""

from __future__ import annotations

from evals.replay import load_golden_traces, replay_trace


def test_all_golden_traces_replay_with_expected_structure() -> None:
    traces = load_golden_traces()

    # 5 Phase 5/6 traces + the M2 Phase 10 full-flow trace.
    assert len(traces) == 6
    for trace in traces:
        result = replay_trace(trace)
        assert result["tool_sequence"] == list(trace.expected_tools)
        assert result["final_status"] == trace.expected_final_status


def test_full_flow_golden_covers_every_wizard_step() -> None:
    """The Phase 10 full-flow golden must visit every step the contract defines.

    The runtime currently emits ``v1.workflow.step.entered`` once per tab, so
    asserting one entered-event per declared step doubles as a contract
    coverage gate — adding a new step without updating the golden will fail
    here before it ships.
    """

    traces = {t.name: t for t in load_golden_traces()}
    full_flow = traces.get("create-pricing-request-full-flow")
    assert full_flow is not None, "Full-flow golden trace must be present"

    entered_step_ids: list[str] = []
    for event in full_flow.expected_event_sequence:
        if event.get("event_type") == "v1.workflow.step.entered":
            step_id = event.get("step_id")
            assert isinstance(step_id, str)
            entered_step_ids.append(step_id)

    expected_steps = [
        "summary",
        "setup_fee",
        "pricing",
        "pnl",
        "quoting_summary",
        "legal",
        "approvals",
    ]
    assert entered_step_ids == expected_steps, (
        f"Wizard step order drifted: expected {expected_steps}, "
        f"golden trace has {entered_step_ids}"
    )


def test_full_flow_golden_includes_suggestion_events_per_proactive_step() -> None:
    """The setup_fee, pricing, and pnl tabs each have proactive-historical fields.

    The golden must declare a ``v1.suggestion.ready`` (or ``v1.suggestion.no_signal``)
    event for each one so the eval flags a missed fan-out.
    """

    traces = {t.name: t for t in load_golden_traces()}
    full_flow = traces.get("create-pricing-request-full-flow")
    assert full_flow is not None

    expected_proactive = {
        "setup_fee": {
            "standard_commitment_fee",
            "quoted_setup_price",
            "service_request_fee_reversal",
            "emergency_funding_fee",
        },
        "pricing": {
            "default_transaction_fee",
            "default_fx_spread_percent",
            "tier_1_fee",
            "tier_2_fee",
            "tier_3_fee",
        },
        "pnl": {
            "target_margin_percent",
            "target_gm_percent",
        },
    }
    seen: dict[str, set[str]] = {step_id: set() for step_id in expected_proactive}
    for event in full_flow.expected_event_sequence:
        if event.get("event_type") in {"v1.suggestion.ready", "v1.suggestion.no_signal"}:
            step_id = event.get("step_id")
            field_id = event.get("field_id")
            if step_id in seen and isinstance(field_id, str):
                seen[step_id].add(field_id)
    for step_id, expected_fields in expected_proactive.items():
        missing = expected_fields - seen[step_id]
        assert not missing, f"Step '{step_id}' missing suggestion events for: {sorted(missing)}"


def test_full_flow_golden_terminates_with_run_completed() -> None:
    traces = {t.name: t for t in load_golden_traces()}
    full_flow = traces.get("create-pricing-request-full-flow")
    assert full_flow is not None
    last = full_flow.expected_event_sequence[-1]
    assert last["event_type"] == "v1.run.completed"
