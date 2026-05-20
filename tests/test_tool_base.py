"""Tool-base projection and requires_approval tests."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from xframe_agent.auth.jwt import AuthContext
from xframe_agent.tools.base import ToolDefinition
from xframe_agent.tools.priceframe_read import RecalculateQuoteAggregatesTool
from xframe_agent.tools.priceframe_write import CreateQuotationTool, SetFxSpreadTool


class _ProjOut(BaseModel):
    data: dict[str, str]
    debug: dict[str, str] | None = None


class _ProjIn(BaseModel):
    pass


class _ProjTool(ToolDefinition[_ProjIn, _ProjOut]):
    name = "_proj"
    description = "x"
    input_model = _ProjIn
    output_model = _ProjOut
    permission = "agent.quotes.read"
    risk = "READ"
    cost_class = "cheap"
    model_visible_fields = ("data",)


def test_project_for_model_strips_non_visible_fields() -> None:
    projected = _ProjTool.project_for_model(
        {"data": {"k": "v"}, "debug": {"trace": "x"}, "extra": "y"}
    )
    assert projected == {"data": {"k": "v"}}


def test_project_for_model_passthrough_when_unset() -> None:
    class _NoListTool(ToolDefinition[_ProjIn, _ProjOut]):
        name = "_no_list"
        description = "x"
        input_model = _ProjIn
        output_model = _ProjOut
        permission = "agent.quotes.read"
        risk = "READ"
        cost_class = "cheap"

    assert _NoListTool.project_for_model({"data": {"k": "v"}, "extra": "y"}) == {
        "data": {"k": "v"},
        "extra": "y",
    }


def _ctx() -> AuthContext:
    return AuthContext(
        user_id=1,
        role_code="r",
        profile_code="p",
        permissions=("agent.quotes.read", "agent.quotes.recalc", "agent.quotes.create"),
        jwt_raw="t",
        session_id=1,
    )


@pytest.mark.asyncio
async def test_recalculate_does_not_require_approval() -> None:
    tool = RecalculateQuoteAggregatesTool()
    args = tool.input_model.model_validate({"id": 1})
    assert await tool.requires_approval(args, _ctx()) is False


@pytest.mark.asyncio
async def test_write_tool_requires_approval() -> None:
    create = CreateQuotationTool()
    create_args = create.input_model.model_validate(
        {"title": "Test Quote", "customer_id": 1, "currency": "USD"}
    )
    assert await create.requires_approval(create_args, _ctx()) is True

    fx = SetFxSpreadTool()
    fx_args = fx.input_model.model_validate(
        {"corridor_id": 1, "applied_fx_spread": "0.01", "minimum_spread": "0.005"}
    )
    assert await fx.requires_approval(fx_args, _ctx()) is True
