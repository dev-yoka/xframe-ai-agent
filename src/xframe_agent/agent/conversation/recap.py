"""Recap partitioning + the single write batch (create + price; never submit)."""
from __future__ import annotations

from typing import Any

from xframe_agent.tools.priceframe_write import (
    BulkAddCorridorsTool,
    CreateQuotationTool,
)


def _all_specs(contract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for step in contract.get("steps", []):
        for f in step.get("fields", []):
            if isinstance(f.get("id"), str):
                out[f["id"]] = f
    return out


def _asked_ids(contract: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for phase in (contract.get("conversation") or {}).get("phases", []):
        ids.update(phase.get("field_ids", []))
    return ids


def _flat(draft_payload: dict[str, Any]) -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for k, sect in (draft_payload or {}).items():
        if not k.startswith("_") and isinstance(sect, dict):
            flat.update(sect)
    return flat


def build_recap(
    contract: dict[str, Any], draft_payload: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    asked = _asked_ids(contract)
    flat: dict[str, Any] = {}
    for k, sect in (draft_payload or {}).items():
        if k.startswith("_") or not isinstance(sect, dict):
            continue
        flat.update({fk: fv for fk, fv in sect.items() if not fk.startswith("_")})
    collected = {k: v for k, v in flat.items() if k in asked}
    defaulted = {k: v for k, v in flat.items() if k not in asked}
    return collected, defaulted


def _create_tool() -> CreateQuotationTool:
    return CreateQuotationTool()


def _corridors_tool() -> BulkAddCorridorsTool:
    return BulkAddCorridorsTool()


def _corridor_rows(corridor_ids: list[int]) -> list[Any]:
    """Build CorridorDraft objects from a list of numeric corridor IDs."""
    from xframe_agent.tools.priceframe_write import CorridorDraft

    return [CorridorDraft(corridor_id=cid) for cid in corridor_ids]


async def commit_draft(
    contract: dict[str, Any],
    draft_payload: dict[str, Any],
    *,
    auth_ctx: Any,
    priceframe: Any,
) -> dict[str, Any]:
    """Create the draft quotation and add corridors. Never calls submit_for_approval."""
    from xframe_agent.tools.priceframe_write import (
        BulkAddCorridorsInput,
        CreateQuotationInput,
    )

    vals = _flat(draft_payload)
    applied: list[str] = []
    failed: list[dict[str, Any]] = []

    create_args = CreateQuotationInput(
        name=vals.get("sending_partner_name") or "",
        opportunity_type=vals.get("opportunity_type") or "New partner",
        currency=(vals.get("default_fee_currency") or "USD"),
        regions=vals.get("corridor_regions") or [],
        countries=vals.get("corridor_countries") or [],
    )
    out = await _create_tool().execute(create_args, auth_ctx, priceframe)
    data: dict[str, Any] = out.data if getattr(out, "data", None) is not None else {}
    quote_id = data.get("quote_id") or data.get("id")
    applied.append("create_quotation")

    # corridor_corridor_ids is a list[int] of numeric PriceFRAME corridor IDs
    corridor_ids: list[int] = vals.get("corridor_corridor_ids") or []
    if quote_id and corridor_ids:
        try:
            raw_id = int(quote_id)
            await _corridors_tool().execute(
                BulkAddCorridorsInput(
                    quote_id=raw_id,
                    corridors=_corridor_rows(corridor_ids),
                ),
                auth_ctx,
                priceframe,
            )
            applied.append("bulk_add_corridors")
        except Exception as exc:  # noqa: BLE001 — partial failure keeps the created quote
            failed.append({"step": "bulk_add_corridors", "error": str(exc)})

    return {"quote_id": quote_id, "applied": applied, "failed": failed}
