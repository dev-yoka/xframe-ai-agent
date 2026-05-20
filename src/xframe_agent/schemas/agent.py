"""Pydantic schemas for Phase D agent APIs."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ConversationCreate(BaseModel):
    title: str = Field(default="New conversation", min_length=1, max_length=200)
    kind: str = "general"


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    pinned: bool | None = None
    archived: bool | None = None


class ConversationResponse(BaseModel):
    id: str
    title: str
    kind: str = "general"
    pinned: bool
    archived: bool
    created_at: datetime
    updated_at: datetime


class ConversationListResponse(BaseModel):
    conversations: list[ConversationResponse]
    next_cursor: str | None = None
    has_more: bool = False


class MessageCreate(BaseModel):
    content: str = Field(min_length=1)
    source: Literal["text", "voice", "attachment"] = "text"


class MessageResponse(BaseModel):
    id: str
    conversation_id: str
    role: str
    content: str
    source: str
    run_id: str | None
    created_at: datetime


class ConversationDetailResponse(ConversationResponse):
    messages: list[MessageResponse]


class RunCreate(BaseModel):
    content: str = Field(min_length=1)
    source: Literal["text", "voice", "attachment"] = "text"


class RunCreateResponse(BaseModel):
    run_id: str
    status: str


class RunResponse(BaseModel):
    id: str
    conversation_id: str
    status: str
    input_message_id: str | None
    output_message_id: str | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    cancelled_at: datetime | None


class DecisionRequest(BaseModel):
    tool_call_id: str
    decision: Literal["approve", "reject", "edit"]
    edited_args: dict[str, object] | None = None


class ToolSchema(BaseModel):
    name: str
    description: str
    permission: str
    risk: str
    cost_class: str
    input_schema: dict[str, object]


class ToolListResponse(BaseModel):
    tools: list[ToolSchema]


class AttachmentResponse(BaseModel):
    id: str
    filename: str
    content_type: str
    size_bytes: int
    checksum_sha256: str
    status: str
    scan_status: str
    download_url: str | None
    created_at: datetime


class MemoryResponse(BaseModel):
    id: str
    key: str
    value: str
    source: str
    created_at: datetime
    updated_at: datetime


class MemoryListResponse(BaseModel):
    memories: list[MemoryResponse]


class VoiceTranscriptionResponse(BaseModel):
    text: str
    model: str
