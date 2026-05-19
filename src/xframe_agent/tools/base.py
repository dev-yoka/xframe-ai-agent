"""Pydantic-native tool definition base."""

from __future__ import annotations

from typing import ClassVar, Generic, Literal, TypeVar

from pydantic import BaseModel

from xframe_agent.auth.jwt import AuthContext
from xframe_agent.priceframe import PriceFrameClient

InputModel = TypeVar("InputModel", bound=BaseModel)
OutputModel = TypeVar("OutputModel", bound=BaseModel)
Risk = Literal["READ", "LOW_RISK_WRITE", "HIGH_RISK_WRITE"]
CostClass = Literal["cheap", "medium", "expensive"]


class ToolPermissionError(Exception):
    """Raised when a user cannot execute a tool."""


class ToolDefinition(Generic[InputModel, OutputModel]):
    """Base class for model-visible tools."""

    name: ClassVar[str]
    description: ClassVar[str]
    input_model: ClassVar[type[InputModel]]
    output_model: ClassVar[type[OutputModel]]
    permission: ClassVar[str]
    risk: ClassVar[Risk]
    cost_class: ClassVar[CostClass]

    async def requires_approval(self, _args: InputModel, _ctx: AuthContext) -> bool:
        return self.risk != "READ"

    async def execute(
        self,
        args: InputModel,
        ctx: AuthContext,
        priceframe: PriceFrameClient,
    ) -> OutputModel:
        if not ctx.has_permission(self.permission):
            raise ToolPermissionError(f"Missing permission: {self.permission}")
        return await self._execute(args, ctx, priceframe)

    async def _execute(
        self,
        args: InputModel,
        ctx: AuthContext,
        priceframe: PriceFrameClient,
    ) -> OutputModel:
        raise NotImplementedError

    @classmethod
    def to_provider_schema(cls) -> dict[str, object]:
        return {
            "name": cls.name,
            "description": cls.description,
            "parameters": cls.input_model.model_json_schema(),
        }
