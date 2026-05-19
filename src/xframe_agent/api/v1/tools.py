"""Tool discovery endpoint."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from xframe_agent.auth.dependencies import get_auth_context
from xframe_agent.auth.jwt import AuthContext
from xframe_agent.schemas import ToolListResponse, ToolSchema
from xframe_agent.tools.registry import tool_registry

router = APIRouter(tags=["tools"])

AuthDep = Annotated[AuthContext, Depends(get_auth_context)]


@router.get("/tools", response_model=ToolListResponse)
async def list_tools(auth: AuthDep) -> ToolListResponse:
    """Return model-visible tools available to the current user."""

    return ToolListResponse(
        tools=[
            ToolSchema(
                name=tool.name,
                description=tool.description,
                permission=tool.permission,
                risk=tool.risk,
                cost_class=tool.cost_class,
                input_schema=tool.input_model.model_json_schema(),
            )
            for tool in tool_registry.available_for(auth)
        ]
    )
