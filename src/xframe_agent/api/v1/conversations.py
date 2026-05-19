"""Conversation, message, and run control endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from xframe_agent.agent.idempotency import get_replay, store_replay
from xframe_agent.agent.loop import AgentLoop
from xframe_agent.auth.dependencies import get_auth_context
from xframe_agent.auth.jwt import AuthContext
from xframe_agent.db.session import get_session
from xframe_agent.models import AgentConversation, AgentMessage, AgentRun
from xframe_agent.models.agent import utc_now
from xframe_agent.schemas import (
    ConversationCreate,
    ConversationDetailResponse,
    ConversationListResponse,
    ConversationResponse,
    ConversationUpdate,
    MessageCreate,
    MessageResponse,
    RunCreate,
    RunCreateResponse,
)
from xframe_agent.settings import Settings, get_settings
from xframe_agent.worker import enqueue_agent_run

router = APIRouter(tags=["conversations"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AuthDep = Annotated[AuthContext, Depends(get_auth_context)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
IdempotencyHeader = Annotated[str | None, Header(alias="Idempotency-Key")]


@router.post(
    "/conversations",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation(
    payload: ConversationCreate,
    response: Response,
    session: SessionDep,
    auth: AuthDep,
    settings: SettingsDep,
    idempotency_key: IdempotencyHeader = None,
) -> ConversationResponse:
    replay = await get_replay(session, user_id=auth.user_id, key=idempotency_key)
    if replay:
        response.status_code = status.HTTP_200_OK
        response.headers["Idempotency-Replayed"] = "true"
        return ConversationResponse.model_validate(replay.response_payload)

    conversation = AgentConversation(user_id=auth.user_id, title=payload.title)
    session.add(conversation)
    await session.flush()
    result = conversation_response(conversation)
    await store_replay(
        session,
        user_id=auth.user_id,
        key=idempotency_key,
        resource_kind="conversation",
        resource_id=conversation.id,
        response_payload=result.model_dump(mode="json"),
        ttl_seconds=settings.idempotency_ttl_seconds,
    )
    await session.commit()
    return result


@router.get("/conversations", response_model=ConversationListResponse)
async def list_conversations(session: SessionDep, auth: AuthDep) -> ConversationListResponse:
    result = await session.execute(
        select(AgentConversation)
        .where(AgentConversation.user_id == auth.user_id, AgentConversation.deleted_at.is_(None))
        .order_by(AgentConversation.updated_at.desc())
    )
    return ConversationListResponse(
        conversations=[conversation_response(row) for row in result.scalars().all()]
    )


@router.get("/conversations/{conversation_id}", response_model=ConversationDetailResponse)
async def get_conversation(
    conversation_id: str,
    session: SessionDep,
    auth: AuthDep,
) -> ConversationDetailResponse:
    conversation = await require_conversation(session, auth, conversation_id)
    messages_result = await session.execute(
        select(AgentMessage)
        .where(AgentMessage.conversation_id == conversation.id)
        .order_by(AgentMessage.created_at.asc())
        .limit(50)
    )
    base = conversation_response(conversation)
    return ConversationDetailResponse(
        **base.model_dump(),
        messages=[message_response(message) for message in messages_result.scalars().all()],
    )


@router.patch("/conversations/{conversation_id}", response_model=ConversationResponse)
async def update_conversation(
    conversation_id: str,
    payload: ConversationUpdate,
    session: SessionDep,
    auth: AuthDep,
) -> ConversationResponse:
    conversation = await require_conversation(session, auth, conversation_id)
    if payload.title is not None:
        conversation.title = payload.title
    if payload.pinned is not None:
        conversation.pinned = payload.pinned
    if payload.archived is not None:
        conversation.archived = payload.archived
    conversation.updated_at = utc_now()
    await session.commit()
    return conversation_response(conversation)


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(conversation_id: str, session: SessionDep, auth: AuthDep) -> None:
    conversation = await require_conversation(session, auth, conversation_id)
    conversation.deleted_at = utc_now()
    conversation.updated_at = utc_now()
    await session.commit()


@router.post("/conversations/{conversation_id}/messages", response_model=RunCreateResponse)
async def send_message(
    conversation_id: str,
    payload: MessageCreate,
    response: Response,
    session: SessionDep,
    auth: AuthDep,
    settings: SettingsDep,
    idempotency_key: IdempotencyHeader = None,
) -> RunCreateResponse:
    replay = await get_replay(session, user_id=auth.user_id, key=idempotency_key)
    if replay:
        response.headers["Idempotency-Replayed"] = "true"
        return RunCreateResponse.model_validate(replay.response_payload)

    run = await create_run_record(session, auth, conversation_id, payload.content, payload.source)
    await AgentLoop().run(session, run_id=run.id, context=auth)
    result = RunCreateResponse(run_id=run.id, status="completed")
    await store_replay(
        session,
        user_id=auth.user_id,
        key=idempotency_key,
        resource_kind="message_run",
        resource_id=run.id,
        response_payload=result.model_dump(mode="json"),
        ttl_seconds=settings.idempotency_ttl_seconds,
    )
    await session.commit()
    return result


@router.post(
    "/conversations/{conversation_id}/runs",
    response_model=RunCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_run(
    conversation_id: str,
    payload: RunCreate,
    response: Response,
    session: SessionDep,
    auth: AuthDep,
    settings: SettingsDep,
    idempotency_key: IdempotencyHeader = None,
) -> RunCreateResponse:
    replay = await get_replay(session, user_id=auth.user_id, key=idempotency_key)
    if replay:
        response.headers["Idempotency-Replayed"] = "true"
        return RunCreateResponse.model_validate(replay.response_payload)

    run = await create_run_record(session, auth, conversation_id, payload.content, payload.source)
    result = RunCreateResponse(run_id=run.id, status=run.status)
    await store_replay(
        session,
        user_id=auth.user_id,
        key=idempotency_key,
        resource_kind="run",
        resource_id=run.id,
        response_payload=result.model_dump(mode="json"),
        ttl_seconds=settings.idempotency_ttl_seconds,
    )
    await session.commit()

    if settings.run_execution_mode == "inline":
        await AgentLoop().run(session, run_id=run.id, context=auth)
    else:
        await enqueue_agent_run(settings, run_id=run.id, auth_context=auth)
    return result


async def create_run_record(
    session: AsyncSession,
    auth: AuthContext,
    conversation_id: str,
    content: str,
    source: str,
) -> AgentRun:
    conversation = await require_conversation(session, auth, conversation_id)
    message = AgentMessage(
        conversation_id=conversation.id,
        user_id=auth.user_id,
        role="user",
        content=content,
        source=source,
    )
    session.add(message)
    await session.flush()

    run = AgentRun(
        conversation_id=conversation.id,
        user_id=auth.user_id,
        status="queued",
        input_message_id=message.id,
    )
    session.add(run)
    await session.flush()
    message.run_id = run.id
    conversation.updated_at = utc_now()
    return run


async def require_conversation(
    session: AsyncSession,
    auth: AuthContext,
    conversation_id: str,
) -> AgentConversation:
    conversation = await session.get(AgentConversation, conversation_id)
    if (
        conversation is None
        or conversation.user_id != auth.user_id
        or conversation.deleted_at is not None
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return conversation


def conversation_response(conversation: AgentConversation) -> ConversationResponse:
    return ConversationResponse(
        id=conversation.id,
        title=conversation.title,
        pinned=conversation.pinned,
        archived=conversation.archived,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def message_response(message: AgentMessage) -> MessageResponse:
    return MessageResponse(
        id=message.id,
        conversation_id=message.conversation_id,
        role=message.role,
        content=message.content,
        source=message.source,
        run_id=message.run_id,
        created_at=message.created_at,
    )
