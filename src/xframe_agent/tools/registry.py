"""Tool registry and permission-filtered discovery."""

from __future__ import annotations

from typing import Any

from xframe_agent.auth.jwt import AuthContext
from xframe_agent.tools.base import ToolDefinition
from xframe_agent.tools.priceframe_read import (
    GetCurrencyRateTool,
    GetQuotationTool,
    ListCorridorsAvailableTool,
    ListMyQuotationsTool,
    LookupSalesforcePrTool,
    RecalculateQuoteAggregatesTool,
)
from xframe_agent.tools.priceframe_write import (
    BulkAddCorridorsTool,
    CreateQuotationTool,
    PreviewPricingChangeTool,
    SetFxSpreadTool,
    SubmitForApprovalTool,
    UpdateCorridorPricingTool,
)

REGISTERED_TOOLS: tuple[ToolDefinition[Any, Any], ...] = (
    ListMyQuotationsTool(),
    GetQuotationTool(),
    ListCorridorsAvailableTool(),
    GetCurrencyRateTool(),
    LookupSalesforcePrTool(),
    RecalculateQuoteAggregatesTool(),
    PreviewPricingChangeTool(),
    CreateQuotationTool(),
    BulkAddCorridorsTool(),
    UpdateCorridorPricingTool(),
    SetFxSpreadTool(),
    SubmitForApprovalTool(),
)

SCAFFOLDED_TOOLS: tuple[type[ToolDefinition[Any, Any]], ...] = (
    PreviewPricingChangeTool,
    CreateQuotationTool,
    BulkAddCorridorsTool,
    UpdateCorridorPricingTool,
    SetFxSpreadTool,
    SubmitForApprovalTool,
)


class ToolRegistry:
    """Registry with per-user discovery filtering."""

    def __init__(self, tools: tuple[ToolDefinition[Any, Any], ...] = REGISTERED_TOOLS) -> None:
        self._tools = {tool.name: tool for tool in tools}

    def available_for(self, context: AuthContext) -> list[ToolDefinition[Any, Any]]:
        return [tool for tool in self._tools.values() if context.has_permission(tool.permission)]

    def get(self, name: str) -> ToolDefinition[Any, Any] | None:
        return self._tools.get(name)


tool_registry = ToolRegistry()
