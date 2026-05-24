"""Round-trip tests for synced workflow contracts."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

CONTRACTS_DIR = Path(__file__).parent.parent / "src" / "xframe_agent" / "workflows" / "contracts"
REPO_ROOT = Path(__file__).parent.parent


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


def test_contracts_drift_workflow_checks_out_canonical_priceframe_repo() -> None:
    workflow = REPO_ROOT / ".github" / "workflows" / "contracts-drift.yml"

    assert "repository: ghalib-mustafa-bf/PriceFRAME" in workflow.read_text()


def test_sync_check_rejects_stale_synced_contract_files() -> None:
    stale = CONTRACTS_DIR / "stale_contract_v0.json"
    stale.write_text("{}\n")
    env = {
        **os.environ,
        "PRICEFRAME_SHARED_DIR": "/Users/bhairava/WorkSpace/repos/PriceFRAME/shared",
    }

    try:
        result = subprocess.run(  # noqa: S603 - trusted local repository script under test.
            ["./scripts/sync_contracts.sh", "--check"],
            cwd=REPO_ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
    finally:
        stale.unlink(missing_ok=True)

    assert result.returncode == 1
    assert "stale_contract_v0.json" in result.stdout
