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


def _corridor_lookup_tool() -> Any:
    """The read tool that lists active PriceFRAME corridors (with identity)."""
    from xframe_agent.tools.priceframe_read import ListCorridorsAvailableTool

    return ListCorridorsAvailableTool()


def _corridor_rows(corridor_ids: list[int]) -> list[Any]:
    """Build CorridorDraft objects from a list of numeric corridor IDs."""
    from xframe_agent.tools.priceframe_write import CorridorDraft

    return [CorridorDraft(corridor_id=cid) for cid in corridor_ids]


# Map a draft field id to the corridor ``identity`` key it filters on. The
# active-corridor list returns objects shaped like
# ``{"id": 4567, "identity": {"region": ..., "country": ..., ...}}``.
_FILTER_FIELD_TO_IDENTITY: dict[str, str] = {
    "corridor_regions": "region",
    "corridor_countries": "country",
    "corridor_services": "service",
    "transaction_types": "transactionType",
    "payout_currencies": "payoutCurrency",
    "payers": "payer",
}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _corridor_records(data: Any) -> list[dict[str, Any]]:
    """Normalize the lookup tool's ``.data`` into a flat list of corridor dicts.

    PriceFRAME may return the active corridors as a bare list or wrapped in a
    ``corridors``/``data`` envelope, so accept either shape.
    """
    if isinstance(data, list):
        return [c for c in data if isinstance(c, dict)]
    if isinstance(data, dict):
        for key in ("corridors", "data", "items", "results"):
            inner = data.get(key)
            if isinstance(inner, list):
                return [c for c in inner if isinstance(c, dict)]
    return []


def _identity_value(corridor: dict[str, Any], identity_key: str) -> Any:
    """Read an identity attribute, tolerating flat or nested ``identity`` shapes."""
    identity = corridor.get("identity")
    if isinstance(identity, dict) and identity_key in identity:
        return identity.get(identity_key)
    return corridor.get(identity_key)


def _corridor_id(corridor: dict[str, Any]) -> int | None:
    raw = corridor.get("id")
    if raw is None:
        raw = corridor.get("corridorId") or corridor.get("corridor_id")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


async def _resolve_corridor_ids(
    vals: dict[str, Any],
    *,
    auth_ctx: Any,
    priceframe: Any,
) -> list[int]:
    """Resolve selected corridor region/country (+ optional filters) to integer ids.

    Lists the active PriceFRAME corridors via the lookup tool and keeps only
    the corridors whose ``identity`` matches every selected filter the user
    supplied. ``corridor_regions``/``corridor_countries`` drive the match;
    ``corridor_services``/``transaction_types``/``payout_currencies``/``payers``
    further narrow it when present. Returns a de-duplicated, ordered list of ids.
    """
    selectors: dict[str, set[Any]] = {}
    for field_id, identity_key in _FILTER_FIELD_TO_IDENTITY.items():
        selected = _as_list(vals.get(field_id))
        if selected:
            selectors[identity_key] = {s for s in selected if s not in (None, "")}

    if not selectors:
        return []

    # Request JSON array format explicitly — the default is NDJSON which
    # causes json.JSONDecodeError ("Extra data") when parsed as a single object.
    jwt_raw: str = getattr(auth_ctx, "jwt_raw", "")
    data = await priceframe.get_json(
        "/api/corridors/active", jwt_raw=jwt_raw, params={"format": "json"}
    )
    records = _corridor_records(data)

    matched: list[int] = []
    seen: set[int] = set()
    for corridor in records:
        if all(
            _identity_value(corridor, identity_key) in wanted
            for identity_key, wanted in selectors.items()
        ):
            cid = _corridor_id(corridor)
            if cid is not None and cid not in seen:
                seen.add(cid)
                matched.append(cid)
    return matched


def _nonempty(value: Any) -> bool:
    """Return True when a value is worth including in the payload."""
    if value is None:
        return False
    if isinstance(value, str | list | dict):
        return bool(value)
    return True


def _map_fee_type(raw: Any) -> str:
    """Map the contract enum label to the PriceFRAME internal value.

    PriceFRAME setup fee store uses ``'setup' | 'network'``, not the display
    labels from the contract enum_options.
    """
    label = str(raw or "").lower()
    if "network" in label:
        return "network"
    return "setup"


def _map_payment_schedule(raw: Any) -> str:
    """Map the contract enum label to the PriceFRAME internal value.

    PriceFRAME uses ``'100' | '50' | 'custom'`` not the full display strings.
    """
    label = str(raw or "").strip()
    if label.startswith("50"):
        return "50"
    if label.lower() == "custom":
        return "custom"
    return "100"  # default / "100% on signature"


def _build_quote_payload(vals: dict[str, Any]) -> dict[str, Any]:
    """Map all collected conversation answers to the PriceFRAME quote payload.

    Key names and value formats are verified against the actual PriceFRAME
    frontend store code (quotes.current.store.ts + openQuoteForEdit.ts) to
    ensure every field hydrates correctly when the user opens the quote.

    Snapshot buckets:
      quotingDetailsSnapshot   → partner context, contract length, show-FX flags
      technicalDetailsSnapshot → currency config, pricing strategy, FX pricing
      pricingToolSnapshot      → setup fee, commitment fee, fee type, pricing rates
      pricingProjectionsSnapshot → P&L targets (wrapped in plData sub-object)
    """
    fee_currency = vals.get("default_fee_currency") or "USD"
    funding_currencies = vals.get("funding_currencies") or [fee_currency]
    source_currency = vals.get("source_currency") or fee_currency

    # ── Top-level structured fields ────────────────────────────────────────
    # These match the QuoteCreateSchema and are read directly by the backend.
    payload: dict[str, Any] = {
        "name": vals.get("sending_partner_name") or "",
        "opportunityType": vals.get("opportunity_type") or "New partner",
        "regions": vals.get("corridor_regions") or [],
        "countries": vals.get("corridor_countries") or [],
        "services": vals.get("corridor_services") or [],
        "transactionTypes": vals.get("transaction_types") or [],
        "payoutCurrencies": vals.get("payout_currencies") or [],
        "payers": vals.get("payers") or [],
        "fundingCurrency": fee_currency,
        "fundingCurrencies": funding_currencies
        if isinstance(funding_currencies, list)
        else [funding_currencies],
        "sourceCurrency": source_currency,
        "defaultFeeCurrency": fee_currency,
        "useCases": vals.get("use_cases") or [],
        # selectedFxPricing / selectedPricingStrategy also accepted top-level
        "selectedFxPricing": vals.get("fx_pricing") or "FX Spread",
        "selectedPricingStrategy": "Corridor Pricing",
    }
    if vals.get("contract_length_years") is not None:
        payload["contractLength"] = int(vals["contract_length_years"])
    if vals.get("waived_months") is not None:
        payload["waivedMonths"] = int(vals["waived_months"])

    # ── quotingDetailsSnapshot ─────────────────────────────────────────────
    # Read by openQuoteForEdit via `qd.*` keys. Verified from store.ts:891-902.
    quoting: dict[str, Any] = {
        "sendingPartnerName": vals.get("sending_partner_name") or "",
        "sendingPartnerRegion": vals.get("sending_partner_region"),
        "sendingPartnerCountry": vals.get("sending_partner_country"),
        "opportunityOwner": vals.get("opportunity_owner"),
        "prCode": vals.get("pr_code"),
        # show-FX flags read as qd.showFXSource / qd.showFXSpread
        "showFXSource": vals.get("show_fx_source_in_contract"),
        "showFXSpread": vals.get("show_fx_spread_in_contract"),
        "contractLength": int(vals["contract_length_years"])
        if vals.get("contract_length_years") is not None
        else None,
        "waivedMonths": int(vals["waived_months"])
        if vals.get("waived_months") is not None
        else None,
        # useCases also stored in qd for hydration fallback
        "useCases": vals.get("use_cases") or [],
    }
    payload["quotingDetailsSnapshot"] = {k: v for k, v in quoting.items() if _nonempty(v)}

    # ── technicalDetailsSnapshot ───────────────────────────────────────────
    # Read by store.ts:408-423 via `td.*` keys.
    # NOTE: integration_type and fx_model are NOT in technicalDetailsSnapshot —
    # they are stored in the TechnicalDetails form component state separately.
    # The technicalDetailsSnapshot stores currency/FX config.
    tech: dict[str, Any] = {
        "selectedPricingStrategy": "Corridor Pricing",
        "selectedFxPricing": vals.get("fx_pricing") or "FX Spread",
        "fundingCurrency": fee_currency,
        "fundingCurrencies": funding_currencies
        if isinstance(funding_currencies, list)
        else [funding_currencies],
        "sourceCurrency": source_currency,
        "defaultFeeCurrency": fee_currency,
        "feeDebitedFrom": fee_currency,
    }
    payload["technicalDetailsSnapshot"] = tech

    # ── pricingToolSnapshot ────────────────────────────────────────────────
    # Read by openQuoteForEdit (line 14-52) and store.ts:433-468 via `pts.*`.
    # CRITICAL: feeType uses 'setup'|'network', paymentSchedule uses '100'|'50'|'custom',
    # and quoted_setup_price is salesRepQuotedPrice. Verified from useUIStateStore.ts.
    pricing_tool: dict[str, Any] = {
        "feeType": _map_fee_type(vals.get("fee_type")),
        "paymentSchedule": _map_payment_schedule(vals.get("payment_schedule")),
        # salesRepQuotedPrice = the agent-collected "Quoting Price" (quoted_setup_price)
        "salesRepQuotedPrice": str(vals["quoted_setup_price"])
        if vals.get("quoted_setup_price") is not None
        else "",
        # networkJoiningFee — same value as salesRepQuotedPrice when feeType='network'
        "networkJoiningFee": float(vals["quoted_setup_price"])
        if vals.get("quoted_setup_price") is not None
        else 0,
        "standardCommitmentFee": float(vals["standard_commitment_fee"])
        if vals.get("standard_commitment_fee") is not None
        else 0,
        "waivedMonths": int(vals["waived_months"])
        if vals.get("waived_months") is not None
        else 0,
        "mcfType": "standard",
        "commitmentFeeDiscount": 0,
    }
    # Additional optional fees stored as otherFees array (PriceFRAME's format)
    other_fees: list[dict[str, Any]] = []
    if vals.get("emergency_funding_fee") is not None:
        other_fees.append({
            "concept": "Emergency Funding Fee",
            "fee": float(vals["emergency_funding_fee"]),
            "type": "emergency_funding",
        })
    if vals.get("service_request_fee_reversal") is not None:
        other_fees.append({
            "concept": "Service Request Fee (Reversal)",
            "fee": float(vals["service_request_fee_reversal"]),
            "type": "service_request_reversal",
        })
    if other_fees:
        pricing_tool["otherFees"] = other_fees
    payload["pricingToolSnapshot"] = pricing_tool

    # ── pricingProjectionsSnapshot ─────────────────────────────────────────
    # Read by store.ts:528 via ui.setPLTabState(snapshot). The plTab state
    # shape wraps targets inside `plData`. Verified from useUIStateStore.ts.
    pnl_data: dict[str, Any] = {}
    if vals.get("target_margin_percent") is not None:
        pnl_data["targetMarginPercent"] = float(vals["target_margin_percent"])
    if vals.get("target_gm_percent") is not None:
        pnl_data["targetGmPercent"] = float(vals["target_gm_percent"])
    if vals.get("pl_discount_type") is not None:
        pnl_data["discountType"] = vals["pl_discount_type"]
    if pnl_data:
        payload["pricingProjectionsSnapshot"] = {"plData": pnl_data}

    # Strip top-level None / empty-list values (snapshots already filtered)
    return {k: v for k, v in payload.items() if _nonempty(v)}


async def commit_draft(
    contract: dict[str, Any],
    draft_payload: dict[str, Any],
    *,
    auth_ctx: Any,
    priceframe: Any,
) -> dict[str, Any]:
    """Create the draft quotation with all collected answers. Never calls submit_for_approval."""
    from xframe_agent.tools.priceframe_write import BulkAddCorridorsInput

    vals = _flat(draft_payload)
    applied: list[str] = []
    failed: list[dict[str, Any]] = []

    # Build comprehensive payload from all conversation answers and call the
    # API directly so every collected field is saved — not just the 5 that
    # CreateQuotationTool accepts.
    jwt_raw: str = getattr(auth_ctx, "jwt_raw", "")
    quote_payload = _build_quote_payload(vals)
    raw: dict[str, Any] = await priceframe.post_json(
        "/api/quotes", jwt_raw=jwt_raw, json=quote_payload
    )
    # PriceFRAME returns { success: true, data: { id: <int>, ... } }.
    _raw_data = raw.get("data")
    nested: dict[str, Any] = _raw_data if isinstance(_raw_data, dict) else {}
    quote_id = nested.get("id") or raw.get("quote_id") or raw.get("id")
    applied.append("create_quotation")

    # Corridor ids may be supplied directly (list[int]) or resolved from the
    # user's selected region/countries at commit time. Direct ids win; otherwise
    # we map the selected geography (+ optional filters) to active corridor ids.
    corridor_ids: list[int] = vals.get("corridor_corridor_ids") or []
    if not corridor_ids and (vals.get("corridor_countries") or vals.get("corridor_regions")):
        try:
            corridor_ids = await _resolve_corridor_ids(
                vals, auth_ctx=auth_ctx, priceframe=priceframe
            )
        except Exception as exc:  # noqa: BLE001 — resolution failure keeps the created quote
            failed.append({"step": "resolve_corridor_ids", "error": str(exc)})
            corridor_ids = []

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
