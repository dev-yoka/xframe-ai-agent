"""PriceFRAME write tools registered in Phase E."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, Field, model_validator

from xframe_agent.auth.jwt import AuthContext
from xframe_agent.priceframe import PriceFrameClient
from xframe_agent.tools.base import ToolDefinition


class JsonPayloadInput(BaseModel):
    payload: dict[str, Any] = Field(default_factory=dict)


class QuoteScopedPayloadInput(JsonPayloadInput):
    quote_id: int = Field(gt=0)


class CorridorScopedPayloadInput(JsonPayloadInput):
    corridor_id: int = Field(gt=0)


class CorridorDraft(BaseModel):
    """A corridor specification for bulk add operations."""

    corridor_id: int = Field(gt=0, description="The corridor ID")
    volume: Decimal | None = Field(default=None, description="Trade volume (optional)")
    term_months: int | None = Field(default=None, ge=1, description="Term in months (optional)")
    applied_rate: Decimal | None = Field(default=None, description="Applied rate (optional)")
    fx_spread: Decimal | None = Field(default=None, description="FX spread (optional)")


class CreateQuotationInput(BaseModel):
    """Typed input for create_quotation tool."""

    name: str | None = Field(default=None, min_length=1, description="Quotation name")
    title: str | None = Field(
        default=None,
        min_length=1,
        description="Legacy alias for quotation name",
    )
    opportunity_type: str = Field(default="New partner", min_length=1)
    customer_id: int | None = Field(default=None, gt=0, description="Legacy customer ID")
    currency: str = Field(default="USD", min_length=3, max_length=3)
    partner_name: str | None = Field(default=None)
    salesforce_pr_id: str | None = Field(default=None)
    regions: list[str] = Field(default_factory=list)
    countries: list[str] = Field(default_factory=list)
    notes: str | None = Field(default=None, description="Optional notes")

    @model_validator(mode="after")
    def normalize_name_and_currency(self) -> CreateQuotationInput:
        if self.name is None and self.title is not None:
            self.name = self.title
        if self.name is None or not self.name.strip():
            raise ValueError("name is required")
        self.name = self.name.strip()
        self.currency = self.currency.upper()
        return self


class BulkAddCorridorsInput(BaseModel):
    """Typed input for bulk_add_corridors tool."""

    quote_id: int = Field(gt=0, description="Quote ID")
    corridors: list[CorridorDraft] = Field(min_length=1, description="Corridors to add")


class UpdateCorridorPricingInput(BaseModel):
    """Typed input for update_corridor_pricing tool."""

    corridor_id: int = Field(gt=0, description="Corridor ID")
    applied_rate: Decimal | None = Field(default=None, description="Applied rate (optional)")
    fx_spread: Decimal | None = Field(default=None, description="FX spread (optional)")
    volume: Decimal | None = Field(default=None, description="Trade volume (optional)")
    term_months: int | None = Field(default=None, ge=1, description="Term in months (optional)")


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


class CreateQuotationTool(ToolDefinition[CreateQuotationInput, JsonOutput]):
    name = "create_quotation"
    description = "Create a draft quotation in PriceFRAME with a title, customer, and currency."
    input_model = CreateQuotationInput
    output_model = JsonOutput
    permission = "agent.quotes.create"
    risk = "LOW_RISK_WRITE"
    cost_class = "medium"

    async def _execute(
        self,
        args: CreateQuotationInput,
        ctx: AuthContext,
        priceframe: PriceFrameClient,
    ) -> JsonOutput:
        currency = args.currency.upper()
        payload: dict[str, Any] = {
            "name": args.name,
            "opportunityType": args.opportunity_type,
            "regions": args.regions,
            "countries": args.countries,
            "fundingCurrency": currency,
            "fundingCurrencies": [currency],
            "sourceCurrency": currency,
            "defaultFeeCurrency": currency,
        }
        quoting_details: dict[str, Any] = {}
        if args.partner_name:
            quoting_details["partnerName"] = args.partner_name
        if args.customer_id is not None:
            quoting_details["customerId"] = args.customer_id
        if quoting_details:
            payload["quotingDetailsSnapshot"] = quoting_details
        if args.salesforce_pr_id:
            payload["salesforcePrId"] = args.salesforce_pr_id
        if args.notes:
            payload["notes"] = args.notes
        return JsonOutput(
            data=await priceframe.post_json(
                "/api/quotes",
                jwt_raw=ctx.jwt_raw,
                json=payload,
            )
        )


class BulkAddCorridorsTool(ToolDefinition[BulkAddCorridorsInput, JsonOutput]):
    name = "bulk_add_corridors"
    description = "Add multiple corridors to a PriceFRAME quotation."
    input_model = BulkAddCorridorsInput
    output_model = JsonOutput
    permission = "agent.quotes.edit"
    risk = "LOW_RISK_WRITE"
    cost_class = "medium"

    async def _execute(
        self,
        args: BulkAddCorridorsInput,
        ctx: AuthContext,
        priceframe: PriceFrameClient,
    ) -> JsonOutput:
        corridors_payload: list[dict[str, Any]] = []
        for corridor in args.corridors:
            corridor_dict: dict[str, Any] = {"corridorId": corridor.corridor_id}
            if corridor.volume is not None:
                corridor_dict["volume"] = str(corridor.volume)
            if corridor.term_months is not None:
                corridor_dict["termMonths"] = corridor.term_months
            if corridor.applied_rate is not None:
                corridor_dict["appliedRate"] = str(corridor.applied_rate)
            if corridor.fx_spread is not None:
                corridor_dict["fxSpread"] = str(corridor.fx_spread)
            corridors_payload.append(corridor_dict)

        payload = {"quoteId": args.quote_id, "corridors": corridors_payload}
        return JsonOutput(
            data=await priceframe.post_json(
                f"/api/quotes/{args.quote_id}/corridors/bulk",
                jwt_raw=ctx.jwt_raw,
                json=payload,
            )
        )


class UpdateCorridorPricingTool(ToolDefinition[UpdateCorridorPricingInput, JsonOutput]):
    name = "update_corridor_pricing"
    description = "Update pricing fields on one quote corridor."
    input_model = UpdateCorridorPricingInput
    output_model = JsonOutput
    permission = "agent.quotes.edit"
    risk = "LOW_RISK_WRITE"
    cost_class = "medium"

    async def _execute(
        self,
        args: UpdateCorridorPricingInput,
        ctx: AuthContext,
        priceframe: PriceFrameClient,
    ) -> JsonOutput:
        payload: dict[str, Any] = {}
        if args.applied_rate is not None:
            payload["appliedRate"] = str(args.applied_rate)
        if args.fx_spread is not None:
            payload["fxSpread"] = str(args.fx_spread)
        if args.volume is not None:
            payload["volume"] = str(args.volume)
        if args.term_months is not None:
            payload["termMonths"] = args.term_months
        return JsonOutput(
            data=await priceframe.put_json(
                f"/api/quote-corridors/{args.corridor_id}",
                jwt_raw=ctx.jwt_raw,
                json=payload,
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
