"""Round-trip tests for synced workflow contracts."""

from __future__ import annotations

import json
from pathlib import Path

CONTRACTS_DIR = Path(__file__).parent.parent / "src" / "xframe_agent" / "workflows" / "contracts"


def test_create_pricing_request_json_exists() -> None:
    path = CONTRACTS_DIR / "create_pricing_request_v1.json"

    assert path.exists(), "run scripts/sync_contracts.sh"


def test_create_pricing_request_parses_with_pydantic() -> None:
    from xframe_agent.workflows.models.create_pricing_request_v1 import WorkflowContract

    path = CONTRACTS_DIR / "create_pricing_request_v1.json"
    data = json.loads(path.read_text())

    parsed = WorkflowContract.model_validate(data)

    assert parsed.id == "create_pricing_request"
    assert parsed.version == "v1"
    assert len(parsed.steps) == 7


def test_round_trip_serialization_is_lossless() -> None:
    from xframe_agent.workflows.models.create_pricing_request_v1 import WorkflowContract

    path = CONTRACTS_DIR / "create_pricing_request_v1.json"
    data = json.loads(path.read_text())

    parsed = WorkflowContract.model_validate(data)
    redumped = json.loads(parsed.model_dump_json(exclude_none=True))

    assert redumped == data, "round-trip changed the document"
