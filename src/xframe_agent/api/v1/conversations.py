"""Conversation, message, run control, and workflow draft endpoints."""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from xframe_agent.agent.dispatch import execute_run
from xframe_agent.agent.idempotency import get_replay, store_replay
from xframe_agent.auth.dependencies import get_auth_context
from xframe_agent.auth.jwt import AuthContext
from xframe_agent.db.session import get_session
from xframe_agent.models import AgentConversation, AgentMessage, AgentRun, AgentWorkflowDraft
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
    WorkflowDraftResponse,
    WorkflowDraftSaveRequest,
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

    conversation = AgentConversation(user_id=auth.user_id, title=payload.title, kind=payload.kind)
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
async def list_conversations(
    session: SessionDep,
    auth: AuthDep,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
) -> ConversationListResponse:
    query = (
        select(AgentConversation)
        .where(AgentConversation.user_id == auth.user_id, AgentConversation.deleted_at.is_(None))
        .order_by(AgentConversation.id.desc())
        .limit(limit + 1)
    )
    if cursor is not None:
        query = query.where(AgentConversation.id < cursor)
    result = await session.execute(query)
    rows = result.scalars().all()
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = page[-1].id if has_more and page else None
    return ConversationListResponse(
        conversations=[conversation_response(row) for row in page],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.get("/conversations/{conversation_id}/draft", response_model=WorkflowDraftResponse)
async def get_workflow_draft(
    conversation_id: str,
    session: SessionDep,
    auth: AuthDep,
) -> WorkflowDraftResponse:
    conversation = await require_conversation(session, auth, conversation_id)
    draft = await session.get(AgentWorkflowDraft, conversation.id)
    if draft is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Workflow draft not found"
        )
    if _is_expired(draft):
        await session.delete(draft)
        await session.commit()
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Workflow draft expired")
    return workflow_draft_response(draft)


@router.post("/conversations/{conversation_id}/draft", response_model=WorkflowDraftResponse)
async def save_workflow_draft(
    conversation_id: str,
    payload: WorkflowDraftSaveRequest,
    session: SessionDep,
    auth: AuthDep,
) -> WorkflowDraftResponse:
    conversation = await require_conversation(session, auth, conversation_id)
    draft = await session.get(AgentWorkflowDraft, conversation.id)
    now = utc_now()
    expires_at = now + timedelta(days=7)
    if draft is None:
        draft = AgentWorkflowDraft(
            conversation_id=conversation.id,
            contract_id=payload.contract_id,
            contract_version=payload.contract_version,
            current_step_id=payload.current_step_id,
            payload=dict(payload.payload),
            step_status=dict(payload.step_status),
            created_at=now,
            updated_at=now,
            expires_at=expires_at,
        )
        session.add(draft)
    else:
        draft.contract_id = payload.contract_id
        draft.contract_version = payload.contract_version
        draft.current_step_id = payload.current_step_id
        draft.payload = dict(payload.payload)
        draft.step_status = dict(payload.step_status)
        draft.updated_at = now
        draft.expires_at = expires_at
    conversation.updated_at = now
    await session.commit()
    return workflow_draft_response(draft)


@router.delete("/conversations/{conversation_id}/draft", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow_draft(
    conversation_id: str,
    session: SessionDep,
    auth: AuthDep,
) -> None:
    conversation = await require_conversation(session, auth, conversation_id)
    draft = await session.get(AgentWorkflowDraft, conversation.id)
    if draft is not None:
        await session.delete(draft)
        conversation.updated_at = utc_now()
        await session.commit()


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
    executed_run = await execute_run(session, settings=settings, run_id=run.id, context=auth)
    result = RunCreateResponse(run_id=run.id, status=executed_run.status)
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
        await execute_run(session, settings=settings, run_id=run.id, context=auth)
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
        kind=conversation.kind or "general",
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


def workflow_draft_response(draft: AgentWorkflowDraft) -> WorkflowDraftResponse:
    return WorkflowDraftResponse(
        conversation_id=draft.conversation_id,
        contract_id=draft.contract_id,
        contract_version=draft.contract_version,
        current_step_id=draft.current_step_id,
        payload=draft.payload,
        step_status=draft.step_status,
        created_at=draft.created_at,
        updated_at=draft.updated_at,
        expires_at=draft.expires_at,
    )


def _is_expired(draft: AgentWorkflowDraft) -> bool:
    expires_at = draft.expires_at
    if expires_at.tzinfo is None:
        return expires_at < utc_now().replace(tzinfo=None)
    return expires_at < utc_now()
