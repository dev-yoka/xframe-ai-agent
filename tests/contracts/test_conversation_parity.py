"""Cross-repo contract parity: the conversation projection must only reference
field ids that exist in the steps, and money fields must carry
requires_explicit_confirm = True.
"""
from __future__ import annotations

import json
from pathlib import Path

CONTRACT = Path("src/xframe_agent/workflows/contracts/create_pricing_request_v1.json")

MONEY_FIELD_IDS = [
    "default_transaction_fee",
    "default_fx_spread_percent",
    "quoted_setup_price",
    "target_margin_percent",
]


def _load() -> dict:
    return json.loads(CONTRACT.read_text())


def test_contract_file_exists() -> None:
    assert CONTRACT.exists(), f"Contract file missing: {CONTRACT}"


def test_conversation_block_is_present() -> None:
    data = _load()
    assert "conversation" in data, "Contract is missing top-level 'conversation' key"
    phases = data["conversation"].get("phases", [])
    assert len(phases) > 0, "conversation.phases must be non-empty"


def test_conversation_phase_ids_are_declared() -> None:
    data = _load()
    phase_ids = [p["id"] for p in data["conversation"]["phases"]]
    for expected in (
        "identity", "partner_detail", "pricing_structure",
        "currencies", "core_pricing", "setup_fee",
        "additional_fees", "pnl", "quoting_summary", "legal",
    ):
        assert expected in phase_ids, f"Phase '{expected}' missing"


def test_conversation_field_ids_exist_in_steps() -> None:
    data = _load()
    all_ids = {f["id"] for s in data["steps"] for f in s["fields"]}
    for phase in data["conversation"]["phases"]:
        for fid in phase["field_ids"]:
            assert fid in all_ids, (
                f"conversation phase '{phase['id']}' references unknown field id: {fid}"
            )


def test_money_fields_have_requires_explicit_confirm() -> None:
    data = _load()
    by_id = {f["id"]: f for s in data["steps"] for f in s["fields"]}
    for fid in MONEY_FIELD_IDS:
        assert fid in by_id, f"Expected money field not found in contract: {fid}"
        assert by_id[fid].get("requires_explicit_confirm") is True, (
            f"Field '{fid}' must have requires_explicit_confirm=true"
        )


def test_identity_phase_contains_expected_fields() -> None:
    data = _load()
    phases = {p["id"]: p for p in data["conversation"]["phases"]}
    identity_fields = set(phases["identity"]["field_ids"])
    for fid in ("sending_partner_name", "opportunity_type", "corridor_regions", "corridor_countries"):
        assert fid in identity_fields, f"'{fid}' missing from identity phase"


def test_money_fields_are_in_conversation() -> None:
    """Money fields may be spread across pricing + targets phases."""
    data = _load()
    all_conv_fields = {
        fid
        for p in data["conversation"]["phases"]
        for fid in p["field_ids"]
    }
    for fid in MONEY_FIELD_IDS:
        assert fid in all_conv_fields, f"'{fid}' missing from conversation phases"
