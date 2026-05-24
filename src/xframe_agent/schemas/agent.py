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


class WorkflowDraftSaveRequest(BaseModel):
    contract_id: str = Field(min_length=1, max_length=128)
    contract_version: str = Field(min_length=1, max_length=32)
    current_step_id: str = Field(min_length=1, max_length=64)
    payload: dict[str, object] = Field(default_factory=dict)
    step_status: dict[str, object] = Field(default_factory=dict)


class WorkflowDraftResponse(WorkflowDraftSaveRequest):
    conversation_id: str
    created_at: datetime
    updated_at: datetime
    expires_at: datetime


class RunCreate(BaseModel):
    content: str = Field(min_length=1)
    source: Literal["text", "voice", "attachment"] = "text"


class RunCreateResponse(BaseModel):
    run_id: str
    status: str


class PendingToolCallResponse(BaseModel):
    id: str
    tool_name: str
    status: str
    args: dict[str, object]
    requires_approval: bool


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
    pending_tool_calls: list[PendingToolCallResponse] = Field(default_factory=list)


class DecisionRequest(BaseModel):
    tool_call_id: str = Field(min_length=1)
    decision: Literal["approve", "reject", "edit"]
    edited_args: dict[str, object] | None = None


class StepAdvanceRequest(BaseModel):
    """Request a per-tab step advance evaluation.

    The wizard posts this when the user clicks Next on a per-tab step. The
    agent runner resolves the contract's ``on_complete`` block against the
    persisted draft and returns one of: ``approved`` (no card needed),
    ``requested`` (proposal awaiting decision), or ``blocked``.
    """

    step_id: str = Field(min_length=1, max_length=64)


class StepAdvanceToolCall(BaseModel):
    tool_call_id: str
    tool_name: str
    args: dict[str, object]
    requires_approval: bool


class StepAdvanceResponse(BaseModel):
    step_id: str
    status: Literal["approved", "requested", "blocked"]
    tool_calls: list[StepAdvanceToolCall] = Field(default_factory=list)
    reason: str | None = None
    detail: dict[str, object] | None = None


class StepDecisionRequest(BaseModel):
    """Approve or reject a previously emitted bundled step proposal."""

    step_id: str = Field(min_length=1, max_length=64)
    decision: Literal["approve", "reject"]
    reason: str | None = Field(default=None, max_length=2000)


class StepDecisionResponse(BaseModel):
    step_id: str
    status: Literal["approved", "rejected"]
    tool_call_results: list[dict[str, object]] = Field(default_factory=list)
    reason: str | None = None


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
