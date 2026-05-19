"""Registered Phase D tools wrapping PriceFRAME REST APIs."""

from __future__ import annotations

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
