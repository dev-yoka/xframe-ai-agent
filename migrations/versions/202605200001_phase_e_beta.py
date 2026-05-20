"""phase e beta attachments writes memory

Revision ID: 202605200001
Revises: 202605190001
Create Date: 2026-05-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202605200001"
down_revision: str | None = "202605190001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json_type() -> sa.types.TypeEngine[object]:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        return postgresql.JSONB()
    return sa.JSON()


def upgrade() -> None:
    json_type = _json_type()
    op.add_column("agent_tool_calls", sa.Column("priceframe_audit_log_id", sa.Integer()))
    op.add_column("agent_tool_calls", sa.Column("approved_at", sa.DateTime(timezone=True)))
    op.add_column("agent_tool_calls", sa.Column("rejected_at", sa.DateTime(timezone=True)))

    op.create_table(
        "agent_attachments",
        sa.Column("id", sa.String(length=26), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "conversation_id",
            sa.String(length=26),
            sa.ForeignKey("agent_conversations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("message_id", sa.String(length=26), nullable=True),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_bucket", sa.String(length=255), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("scan_status", sa.String(length=32), nullable=False),
        sa.Column("scan_result", sa.Text(), nullable=True),
        sa.Column("metadata", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_agent_attachments_user_id", "agent_attachments", ["user_id"])
    op.create_index(
        "ix_agent_attachments_conversation_id",
        "agent_attachments",
        ["conversation_id"],
    )
    op.create_index("ix_agent_attachments_message_id", "agent_attachments", ["message_id"])
    op.create_index(
        "idx_agent_attachments_user_created",
        "agent_attachments",
        ["user_id", "created_at"],
    )

    op.create_table(
        "agent_attachment_pages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "attachment_id",
            sa.String(length=26),
            sa.ForeignKey("agent_attachments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("metadata", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "attachment_id",
            "page_number",
            name="uq_agent_attachment_pages_page",
        ),
    )
    op.create_index(
        "ix_agent_attachment_pages_attachment_id",
        "agent_attachment_pages",
        ["attachment_id"],
    )

    op.create_table(
        "agent_user_memory",
        sa.Column("id", sa.String(length=26), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("metadata", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_agent_user_memory_user_id", "agent_user_memory", ["user_id"])
    op.create_index(
        "idx_agent_user_memory_user_created",
        "agent_user_memory",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("agent_user_memory")
    op.drop_table("agent_attachment_pages")
    op.drop_table("agent_attachments")
    op.drop_column("agent_tool_calls", "rejected_at")
    op.drop_column("agent_tool_calls", "approved_at")
    op.drop_column("agent_tool_calls", "priceframe_audit_log_id")
