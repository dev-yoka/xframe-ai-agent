"""Attachment upload and metadata endpoints."""

from __future__ import annotations

from hashlib import sha256
from pathlib import PurePath
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from xframe_agent.attachments import scan_bytes, storage_from_settings
from xframe_agent.auth.dependencies import get_auth_context
from xframe_agent.auth.jwt import AuthContext
from xframe_agent.db.session import get_session
from xframe_agent.models import AgentAttachment, AgentConversation
from xframe_agent.models.agent import utc_now
from xframe_agent.schemas import AttachmentResponse
from xframe_agent.settings import Settings, get_settings
from xframe_agent.worker import enqueue_attachment_scan

router = APIRouter(tags=["attachments"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AuthDep = Annotated[AuthContext, Depends(get_auth_context)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.post(
    "/attachments",
    response_model=AttachmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_attachment(
    session: SessionDep,
    auth: AuthDep,
    settings: SettingsDep,
    file: Annotated[UploadFile, File()],
    conversation_id: Annotated[str | None, Form()] = None,
) -> AttachmentResponse:
    if conversation_id is not None:
        conversation = await session.get(AgentConversation, conversation_id)
        if conversation is None or conversation.user_id != auth.user_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation not found",
            )

    data = await file.read()
    if len(data) > settings.attachment_max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Attachment is too large",
        )

    filename = _safe_filename(file.filename)
    checksum = sha256(data).hexdigest()
    content_type = file.content_type or "application/octet-stream"
    attachment = AgentAttachment(
        user_id=auth.user_id,
        conversation_id=conversation_id,
        filename=filename,
        content_type=content_type,
        size_bytes=len(data),
        checksum_sha256=checksum,
        storage_bucket=settings.s3_bucket,
        storage_key="pending",
        status="pending_scan",
        scan_status="pending",
    )
    session.add(attachment)
    await session.flush()

    storage_key = f"users/{auth.user_id}/attachments/{attachment.id}/{filename}"
    storage = storage_from_settings(settings)
    stored = await storage.put_bytes(key=storage_key, data=data, content_type=content_type)
    attachment.storage_bucket = stored.bucket
    attachment.storage_key = stored.key

    if settings.attachment_scan_mode == "arq":
        attachment.scan_status = "pending"
        attachment.status = "pending_scan"
    else:
        scan = await scan_bytes(data, settings)
        attachment.scan_status = scan.status
        attachment.scan_result = scan.detail
        attachment.status = "ready" if scan.is_clean else scan.status
    attachment.updated_at = utc_now()
    await session.commit()
    if settings.attachment_scan_mode == "arq":
        await enqueue_attachment_scan(settings, attachment_id=attachment.id)
    return await attachment_response(attachment, settings)


@router.get("/attachments/{attachment_id}", response_model=AttachmentResponse)
async def get_attachment(
    attachment_id: str,
    session: SessionDep,
    auth: AuthDep,
    settings: SettingsDep,
) -> AttachmentResponse:
    attachment = await session.get(AgentAttachment, attachment_id)
    if attachment is None or attachment.user_id != auth.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")
    return await attachment_response(attachment, settings)


async def attachment_response(
    attachment: AgentAttachment,
    settings: Settings,
) -> AttachmentResponse:
    storage = storage_from_settings(settings)
    download_url = await storage.presigned_get_url(key=attachment.storage_key)
    return AttachmentResponse(
        id=attachment.id,
        filename=attachment.filename,
        content_type=attachment.content_type,
        size_bytes=attachment.size_bytes,
        checksum_sha256=attachment.checksum_sha256,
        status=attachment.status,
        scan_status=attachment.scan_status,
        download_url=download_url,
        created_at=attachment.created_at,
    )


def _safe_filename(filename: str | None) -> str:
    candidate = PurePath(filename or "attachment.bin").name.strip()
    return candidate or "attachment.bin"
