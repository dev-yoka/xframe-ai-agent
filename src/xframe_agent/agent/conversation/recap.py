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


def _build_quote_payload(vals: dict[str, Any]) -> dict[str, Any]:
    """Map all collected conversation answers to the PriceFRAME quote payload.

    Structured fields are sent as top-level keys; everything that lives in
    the Summary/Setup/Pricing/P&L/Legal tabs but lacks a native top-level
    slot is grouped into the appropriate *Snapshot JSON blob so the
    PriceFRAME wizard can surface it when the user opens the form.
    """
    fee_currency = vals.get("default_fee_currency") or "USD"
    funding_currencies = vals.get("funding_currencies") or [fee_currency]
    source_currency = vals.get("source_currency") or fee_currency

    # ── Direct structured fields accepted by POST /api/quotes ─────────────
    payload: dict[str, Any] = {
        "name": vals.get("sending_partner_name") or "",
        "opportunityType": vals.get("opportunity_type") or "New partner",
        # Corridor geography
        "regions": vals.get("corridor_regions") or [],
        "countries": vals.get("corridor_countries") or [],
        "services": vals.get("corridor_services") or [],
        "transactionTypes": vals.get("transaction_types") or [],
        "payoutCurrencies": vals.get("payout_currencies") or [],
        "payers": vals.get("payers") or [],
        # Currency config
        "fundingCurrency": fee_currency,
        "fundingCurrencies": funding_currencies
        if isinstance(funding_currencies, list)
        else [funding_currencies],
        "sourceCurrency": source_currency,
        "defaultFeeCurrency": fee_currency,
        # Contract terms
        "useCases": vals.get("use_cases") or [],
        "selectedFxPricing": vals.get("fx_pricing"),
    }
    if vals.get("contract_length_years") is not None:
        payload["contractLength"] = int(vals["contract_length_years"])
    if vals.get("waived_months") is not None:
        payload["waivedMonths"] = int(vals["waived_months"])

    # ── quotingDetailsSnapshot — partner context, quoting document flags ───
    quoting: dict[str, Any] = {}
    for src_key, dest_key in [
        ("sending_partner_region", "sendingPartnerRegion"),
        ("sending_partner_country", "sendingPartnerCountry"),
        ("opportunity_owner", "opportunityOwner"),
        ("customer_facing_currency", "customerFacingCurrency"),
        ("quote_summary_notes", "quoteSummaryNotes"),
        ("include_setup_fees", "includeSetupFees"),
        ("include_fx_model", "includeFxModel"),
        ("include_pnl_projection", "includePnlProjection"),
        ("include_sd_quotation", "includeSdQuotation"),
        ("include_fee_annex", "includeFeeAnnex"),
        ("include_fx_source", "includeFxSource"),
        ("include_fx_spread", "includeFxSpread"),
        ("legal_comments", "legalComments"),
        ("special_terms", "specialTerms"),
        ("show_fx_source_in_contract", "showFxSourceInContract"),
        ("show_fx_spread_in_contract", "showFxSpreadInContract"),
        ("quote_valid_until", "quoteValidUntil"),
        ("contract_language", "contractLanguage"),
    ]:
        v = vals.get(src_key)
        if _nonempty(v):
            quoting[dest_key] = v
    if quoting:
        payload["quotingDetailsSnapshot"] = quoting

    # ── technicalDetailsSnapshot — integration model choices ───────────────
    tech: dict[str, Any] = {}
    for src_key, dest_key in [
        ("integration_type", "integrationType"),
        ("fx_model", "fxModel"),
        ("fx_pricing", "fxPricing"),
        ("pricing_strategy", "pricingStrategy"),
        ("quantity_funding_currencies", "quantityFundingCurrencies"),
        ("quantity_source_currencies", "quantitySourceCurrencies"),
    ]:
        v = vals.get(src_key)
        if _nonempty(v):
            tech[dest_key] = v
    if tech:
        payload["technicalDetailsSnapshot"] = tech

    # ── pricingToolSnapshot — fee amounts and pricing configuration ─────────
    pricing_tool: dict[str, Any] = {}
    for src_key, dest_key in [
        ("fee_type", "feeType"),
        ("payment_schedule", "paymentSchedule"),
        ("quoted_setup_price", "quotedSetupPrice"),
        ("standard_commitment_fee", "standardCommitmentFee"),
        ("waived_months", "waivedMonths"),
        ("emergency_funding_fee", "emergencyFundingFee"),
        ("service_request_fee_reversal", "serviceRequestFeeReversal"),
        ("service_request_fee_proof", "serviceRequestFeeProof"),
        ("treasury_management_fee", "treasuryManagementFee"),
        ("treasury_management_currencies", "treasuryManagementCurrencies"),
        ("business_hub_platform_fee", "businessHubPlatformFee"),
        ("post_funding_line_penalty_percent", "postFundingLinePenaltyPercent"),
        ("corridor_no_usage_fee", "corridorNoUsageFee"),
        ("white_glove_service_fee", "whiteGloveServiceFee"),
        ("stablecoin_prefunding_setup_fee", "stablecoinPrefundingSetupFee"),
        ("digital_asset_icp_setup_fee", "digitalAssetIcpSetupFee"),
        ("bulk_currency_conversion_fee", "bulkCurrencyConversionFee"),
        ("pricing_mode", "pricingMode"),
        ("corridor_pricing_scope", "corridorPricingScope"),
        ("applied_rate_strategy", "appliedRateStrategy"),
        ("applied_fx_spread_strategy", "appliedFxSpreadStrategy"),
        ("default_transaction_fee", "defaultTransactionFee"),
        ("default_fx_spread_percent", "defaultFxSpreadPercent"),
        ("volume_assumption_usd", "volumeAssumptionUsd"),
        ("tier_1_threshold", "tier1Threshold"),
        ("tier_1_fee", "tier1Fee"),
        ("tier_2_threshold", "tier2Threshold"),
        ("tier_2_fee", "tier2Fee"),
        ("tier_3_threshold", "tier3Threshold"),
        ("tier_3_fee", "tier3Fee"),
    ]:
        v = vals.get(src_key)
        if _nonempty(v):
            pricing_tool[dest_key] = v
    if pricing_tool:
        payload["pricingToolSnapshot"] = pricing_tool

    # ── pricingProjectionsSnapshot — P&L targets ───────────────────────────
    pnl: dict[str, Any] = {}
    for src_key, dest_key in [
        ("pl_discount_type", "plDiscountType"),
        ("target_margin_percent", "targetMarginPercent"),
        ("target_gm_percent", "targetGmPercent"),
        ("growth_rate_year_2", "growthRateYear2"),
        ("growth_rate_year_3", "growthRateYear3"),
    ]:
        v = vals.get(src_key)
        if _nonempty(v):
            pnl[dest_key] = v
    if pnl:
        payload["pricingProjectionsSnapshot"] = pnl

    # Strip top-level None / empty-list values (snapshots already filtered above)
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
    nested = raw.get("data") if isinstance(raw.get("data"), dict) else {}
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
