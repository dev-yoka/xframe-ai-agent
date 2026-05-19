"""phase d agent core

Revision ID: 202605190001
Revises:
Create Date: 2026-05-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202605190001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json_type() -> sa.types.TypeEngine[object]:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        return postgresql.JSONB()
    return sa.JSON()


def upgrade() -> None:
    json_type = _json_type()
    op.create_table(
        "agent_conversations",
        sa.Column("id", sa.String(length=26), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_agent_conversations_user_id", "agent_conversations", ["user_id"])

    op.create_table(
        "agent_messages",
        sa.Column("id", sa.String(length=26), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.String(length=26),
            sa.ForeignKey("agent_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.String(length=26), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_agent_messages_conversation_id", "agent_messages", ["conversation_id"])
    op.create_index("ix_agent_messages_run_id", "agent_messages", ["run_id"])
    op.create_index("ix_agent_messages_user_id", "agent_messages", ["user_id"])

    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(length=26), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.String(length=26),
            sa.ForeignKey("agent_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("input_message_id", sa.String(length=26), nullable=True),
        sa.Column("output_message_id", sa.String(length=26), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_agent_runs_conversation_id", "agent_runs", ["conversation_id"])
    op.create_index("ix_agent_runs_user_id", "agent_runs", ["user_id"])

    op.create_table(
        "agent_run_steps",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "run_id",
            sa.String(length=26),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_agent_run_steps_run_id", "agent_run_steps", ["run_id"])

    op.create_table(
        "agent_tool_calls",
        sa.Column("id", sa.String(length=26), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(length=26),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("step_id", sa.Integer(), nullable=True),
        sa.Column("tool_name", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("args", json_type, nullable=False),
        sa.Column("result", json_type, nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("requires_approval", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_agent_tool_calls_run_id", "agent_tool_calls", ["run_id"])

    op.create_table(
        "agent_run_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "run_id",
            sa.String(length=26),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("payload", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "seq", name="uq_agent_run_events_run_seq"),
    )
    op.create_index("ix_agent_run_events_run_id", "agent_run_events", ["run_id"])

    op.create_table(
        "agent_idempotency_keys",
        sa.Column("user_id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(length=128), primary_key=True),
        sa.Column("resource_kind", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=64), nullable=False),
        sa.Column("response_payload", json_type, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "agent_users_cache",
        sa.Column("user_id", sa.Integer(), primary_key=True),
        sa.Column("role_code", sa.String(length=128), nullable=False),
        sa.Column("profile_code", sa.String(length=128), nullable=False),
        sa.Column("permissions", json_type, nullable=False),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "agent_device_tokens",
        sa.Column("id", sa.String(length=26), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("fcm_token", sa.Text(), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "fcm_token", name="uq_agent_device_tokens_user_token"),
    )
    op.create_index("ix_agent_device_tokens_user_id", "agent_device_tokens", ["user_id"])

    op.create_table(
        "agent_audit_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(length=26), nullable=True),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("payload", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_agent_audit_log_run_id", "agent_audit_log", ["run_id"])
    op.create_index("ix_agent_audit_log_user_id", "agent_audit_log", ["user_id"])
    op.create_index("idx_agent_audit_user_created", "agent_audit_log", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_table("agent_audit_log")
    op.drop_table("agent_device_tokens")
    op.drop_table("agent_users_cache")
    op.drop_table("agent_idempotency_keys")
    op.drop_table("agent_run_events")
    op.drop_table("agent_tool_calls")
    op.drop_table("agent_run_steps")
    op.drop_table("agent_runs")
    op.drop_table("agent_messages")
    op.drop_table("agent_conversations")
