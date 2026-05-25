"""Registered Phase D tools wrapping PriceFRAME REST APIs."""

from __future__ import annotations

import base64
import json
from typing import Any

from pydantic import BaseModel, Field

from xframe_agent.auth.jwt import AuthContext
from xframe_agent.priceframe import PriceFrameClient
from xframe_agent.tools.base import ToolDefinition


class EmptyInput(BaseModel):
    pass


class QuoteListInput(BaseModel):
    status: str | None = None
    limit: int = Field(default=20, ge=1, le=100)


class IdInput(BaseModel):
    id: int = Field(gt=0)


class CurrencyInput(BaseModel):
    currency: str = Field(min_length=3, max_length=3)


class SalesforceLookupInput(BaseModel):
    query: str = Field(min_length=1)


class FieldSuggestionsInput(BaseModel):
    field: str = Field(min_length=1)
    ctx: dict[str, Any] = Field(default_factory=dict)


class JsonOutput(BaseModel):
    data: Any


class ListMyQuotationsTool(ToolDefinition[QuoteListInput, JsonOutput]):
    name = "list_my_quotations"
    description = "List quotations visible to the current Sales Representative."
    input_model = QuoteListInput
    output_model = JsonOutput
    permission = "agent.quotes.read"
    risk = "READ"
    cost_class = "cheap"

    async def _execute(
        self,
        args: QuoteListInput,
        ctx: AuthContext,
        priceframe: PriceFrameClient,
    ) -> JsonOutput:
        params: dict[str, Any] = {"owner_id": "me", "limit": args.limit}
        if args.status:
            params["status"] = args.status
        return JsonOutput(
            data=await priceframe.get_json(
                "/api/quotes",
                jwt_raw=ctx.jwt_raw,
                params=params,
            )
        )


class GetQuotationTool(ToolDefinition[IdInput, JsonOutput]):
    name = "get_quotation"
    description = "Read the composite pricing context for one quotation."
    input_model = IdInput
    output_model = JsonOutput
    permission = "agent.quotes.read"
    risk = "READ"
    cost_class = "cheap"
    model_visible_fields = ("data",)

    async def _execute(
        self,
        args: IdInput,
        ctx: AuthContext,
        priceframe: PriceFrameClient,
    ) -> JsonOutput:
        return JsonOutput(
            data=await priceframe.get_json(
                f"/api/v1/quotes/{args.id}/pricing-context",
                jwt_raw=ctx.jwt_raw,
            )
        )


class ListCorridorsAvailableTool(ToolDefinition[EmptyInput, JsonOutput]):
    name = "list_corridors_available"
    description = "List active PriceFRAME corridors available for quote construction."
    input_model = EmptyInput
    output_model = JsonOutput
    permission = "agent.quotes.read"
    risk = "READ"
    cost_class = "medium"

    async def _execute(
        self,
        args: EmptyInput,
        ctx: AuthContext,
        priceframe: PriceFrameClient,
    ) -> JsonOutput:
        del args
        return JsonOutput(
            data=await priceframe.get_json("/api/corridors/active", jwt_raw=ctx.jwt_raw)
        )


class GetCurrencyRateTool(ToolDefinition[CurrencyInput, JsonOutput]):
    name = "get_currency_rate"
    description = "Read a configured PriceFRAME currency rate."
    input_model = CurrencyInput
    output_model = JsonOutput
    permission = "agent.quotes.read"
    risk = "READ"
    cost_class = "cheap"

    async def _execute(
        self,
        args: CurrencyInput,
        ctx: AuthContext,
        priceframe: PriceFrameClient,
    ) -> JsonOutput:
        return JsonOutput(
            data=await priceframe.get_json(
                "/api/app-config/currency-rates",
                jwt_raw=ctx.jwt_raw,
                params={"currency": args.currency.upper()},
            )
        )


class LookupSalesforcePrTool(ToolDefinition[SalesforceLookupInput, JsonOutput]):
    name = "lookup_salesforce_pr"
    description = "Search PriceFRAME Salesforce pricing request data."
    input_model = SalesforceLookupInput
    output_model = JsonOutput
    permission = "agent.salesforce.read"
    risk = "READ"
    cost_class = "medium"

    async def _execute(
        self,
        args: SalesforceLookupInput,
        ctx: AuthContext,
        priceframe: PriceFrameClient,
    ) -> JsonOutput:
        return JsonOutput(
            data=await priceframe.get_json(
                "/api/quotes/salesforce/search",
                jwt_raw=ctx.jwt_raw,
                params={"q": args.query},
            )
        )


class GetFieldSuggestionsTool(ToolDefinition[FieldSuggestionsInput, JsonOutput]):
    name = "get_field_suggestions"
    description = (
        "Fetch a historical-data suggestion (median/mean/mode/p75) for a workflow "
        "field, scoped by the contextual filter keys (corridor, service, etc.)."
    )
    input_model = FieldSuggestionsInput
    output_model = JsonOutput
    permission = "agent.suggestions.read"
    risk = "READ"
    cost_class = "cheap"

    async def _execute(
        self,
        args: FieldSuggestionsInput,
        ctx: AuthContext,
        priceframe: PriceFrameClient,
    ) -> JsonOutput:
        ctx_payload = json.dumps(args.ctx, separators=(",", ":"), sort_keys=True)
        ctx_b64 = base64.b64encode(ctx_payload.encode("utf-8")).decode("ascii")
        return JsonOutput(
            data=await priceframe.get_json(
                "/api/v1/agent/suggestions",
                jwt_raw=ctx.jwt_raw,
                params={"field": args.field, "ctx": ctx_b64},
            )
        )


class RecalculateQuoteAggregatesTool(ToolDefinition[IdInput, JsonOutput]):
    name = "recalculate_quote_aggregates"
    description = "Ask PriceFRAME to recalculate quotation aggregate values."
    input_model = IdInput
    output_model = JsonOutput
    permission = "agent.quotes.recalc"
    risk = "LOW_RISK_WRITE"
    cost_class = "cheap"

    async def requires_approval(self, _args: IdInput, _ctx: AuthContext) -> bool:
        return False

    async def _execute(
        self,
        args: IdInput,
        ctx: AuthContext,
        priceframe: PriceFrameClient,
    ) -> JsonOutput:
        return JsonOutput(
            data=await priceframe.post_json(
                f"/api/quotes/{args.id}/recalculate-aggregates",
                jwt_raw=ctx.jwt_raw,
            )
        )
