"""PriceFRAME write tools registered in Phase E."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, Field

from xframe_agent.auth.jwt import AuthContext
from xframe_agent.priceframe import PriceFrameClient
from xframe_agent.tools.base import ToolDefinition


class JsonPayloadInput(BaseModel):
    payload: dict[str, Any] = Field(default_factory=dict)


class QuoteScopedPayloadInput(JsonPayloadInput):
    quote_id: int = Field(gt=0)


class CorridorScopedPayloadInput(JsonPayloadInput):
    corridor_id: int = Field(gt=0)


class FxSpreadInput(BaseModel):
    corridor_id: int = Field(gt=0)
    applied_fx_spread: str
    minimum_spread: str


class ApprovalInput(BaseModel):
    quote_id: int = Field(gt=0)
    comment: str | None = None


class JsonOutput(BaseModel):
    data: Any


class PreviewPricingChangeTool(ToolDefinition[QuoteScopedPayloadInput, JsonOutput]):
    name = "preview_pricing_change"
    description = "Preview a pricing change through PriceFRAME without committing it."
    input_model = QuoteScopedPayloadInput
    output_model = JsonOutput
    permission = "agent.quotes.recalc"
    risk = "READ"
    cost_class = "cheap"

    async def _execute(
        self,
        args: QuoteScopedPayloadInput,
        ctx: AuthContext,
        priceframe: PriceFrameClient,
    ) -> JsonOutput:
        return JsonOutput(
            data=await priceframe.post_json(
                f"/api/v1/quotes/{args.quote_id}/pricing/preview",
                jwt_raw=ctx.jwt_raw,
                json=args.payload,
            )
        )


class CreateQuotationTool(ToolDefinition[JsonPayloadInput, JsonOutput]):
    name = "create_quotation"
    description = "Create a draft quotation in PriceFRAME."
    input_model = JsonPayloadInput
    output_model = JsonOutput
    permission = "agent.quotes.create"
    risk = "LOW_RISK_WRITE"
    cost_class = "medium"

    async def _execute(
        self,
        args: JsonPayloadInput,
        ctx: AuthContext,
        priceframe: PriceFrameClient,
    ) -> JsonOutput:
        return JsonOutput(
            data=await priceframe.post_json(
                "/api/quotes",
                jwt_raw=ctx.jwt_raw,
                json=args.payload,
            )
        )


class BulkAddCorridorsTool(ToolDefinition[QuoteScopedPayloadInput, JsonOutput]):
    name = "bulk_add_corridors"
    description = "Add multiple corridors to a PriceFRAME quotation."
    input_model = QuoteScopedPayloadInput
    output_model = JsonOutput
    permission = "agent.quotes.edit"
    risk = "LOW_RISK_WRITE"
    cost_class = "medium"

    async def _execute(
        self,
        args: QuoteScopedPayloadInput,
        ctx: AuthContext,
        priceframe: PriceFrameClient,
    ) -> JsonOutput:
        payload = {"quoteId": args.quote_id, **args.payload}
        return JsonOutput(
            data=await priceframe.post_json(
                f"/api/quotes/{args.quote_id}/corridors/bulk",
                jwt_raw=ctx.jwt_raw,
                json=payload,
            )
        )


class UpdateCorridorPricingTool(ToolDefinition[CorridorScopedPayloadInput, JsonOutput]):
    name = "update_corridor_pricing"
    description = "Update pricing fields on one quote corridor."
    input_model = CorridorScopedPayloadInput
    output_model = JsonOutput
    permission = "agent.quotes.edit"
    risk = "LOW_RISK_WRITE"
    cost_class = "medium"

    async def _execute(
        self,
        args: CorridorScopedPayloadInput,
        ctx: AuthContext,
        priceframe: PriceFrameClient,
    ) -> JsonOutput:
        return JsonOutput(
            data=await priceframe.put_json(
                f"/api/quote-corridors/{args.corridor_id}",
                jwt_raw=ctx.jwt_raw,
                json=args.payload,
            )
        )


class SetFxSpreadTool(ToolDefinition[FxSpreadInput, JsonOutput]):
    name = "set_fx_spread"
    description = "Set a corridor FX spread; hard-blocked below minimum spread in Phase E."
    input_model = FxSpreadInput
    output_model = JsonOutput
    permission = "agent.quotes.edit"
    risk = "LOW_RISK_WRITE"
    cost_class = "cheap"

    async def _execute(
        self,
        args: FxSpreadInput,
        ctx: AuthContext,
        priceframe: PriceFrameClient,
    ) -> JsonOutput:
        try:
            applied = Decimal(args.applied_fx_spread)
            minimum = Decimal(args.minimum_spread)
        except InvalidOperation as exc:
            raise ValueError("FX spread values must be decimal strings") from exc
        if applied < minimum:
            raise ValueError("Applied FX spread is below the minimum spread")

        return JsonOutput(
            data=await priceframe.put_json(
                f"/api/quote-corridors/{args.corridor_id}",
                jwt_raw=ctx.jwt_raw,
                json={
                    "appliedFxSpread": args.applied_fx_spread,
                    "minimumSpread": args.minimum_spread,
                },
            )
        )


class SubmitForApprovalTool(ToolDefinition[ApprovalInput, JsonOutput]):
    name = "submit_for_approval"
    description = "Submit a quotation for approval after explicit user confirmation."
    input_model = ApprovalInput
    output_model = JsonOutput
    permission = "agent.approvals.submit"
    risk = "HIGH_RISK_WRITE"
    cost_class = "medium"

    async def _execute(
        self,
        args: ApprovalInput,
        ctx: AuthContext,
        priceframe: PriceFrameClient,
    ) -> JsonOutput:
        payload: dict[str, Any] = {
            "policy": "quote_pricing",
            "approvers": {"type": "group", "codes": ["pricing_team"]},
            "reasons": {"source": "agent"},
        }
        if args.comment:
            payload["initiatorComment"] = args.comment
        return JsonOutput(
            data=await priceframe.post_json(
                f"/api/quotes/{args.quote_id}/approvals",
                jwt_raw=ctx.jwt_raw,
                json=payload,
            )
        )
