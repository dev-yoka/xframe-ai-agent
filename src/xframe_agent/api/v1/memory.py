"""User-visible memory management endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from xframe_agent.auth.dependencies import get_auth_context
from xframe_agent.auth.jwt import AuthContext
from xframe_agent.db.session import get_session
from xframe_agent.models import AgentUserMemory
from xframe_agent.schemas import MemoryListResponse, MemoryResponse

router = APIRouter(tags=["memory"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AuthDep = Annotated[AuthContext, Depends(get_auth_context)]


@router.get("/memory", response_model=MemoryListResponse)
async def list_memory(session: SessionDep, auth: AuthDep) -> MemoryListResponse:
    result = await session.execute(
        select(AgentUserMemory)
        .where(AgentUserMemory.user_id == auth.user_id)
        .order_by(AgentUserMemory.updated_at.desc())
    )
    return MemoryListResponse(
        memories=[memory_response(memory) for memory in result.scalars().all()]
    )


@router.delete("/memory/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(memory_id: str, session: SessionDep, auth: AuthDep) -> None:
    memory = await session.get(AgentUserMemory, memory_id)
    if memory is None or memory.user_id != auth.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found")
    await session.delete(memory)
    await session.commit()


def memory_response(memory: AgentUserMemory) -> MemoryResponse:
    return MemoryResponse(
        id=memory.id,
        key=memory.key,
        value=memory.value,
        source=memory.source,
        created_at=memory.created_at,
        updated_at=memory.updated_at,
    )
