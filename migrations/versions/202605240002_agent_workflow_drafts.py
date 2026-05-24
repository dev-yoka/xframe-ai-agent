"""agent workflow drafts

Revision ID: 202605240002
Revises: 202605210001
Create Date: 2026-05-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202605240002"
down_revision: str | None = "202605210001"
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
        "agent_workflow_drafts",
        sa.Column(
            "conversation_id",
            sa.String(length=26),
            sa.ForeignKey("agent_conversations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("contract_id", sa.String(length=128), nullable=False),
        sa.Column("contract_version", sa.String(length=32), nullable=False),
        sa.Column("current_step_id", sa.String(length=64), nullable=False),
        sa.Column("payload", json_type, nullable=False),
        sa.Column("step_status", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "idx_agent_workflow_drafts_expires",
        "agent_workflow_drafts",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_agent_workflow_drafts_expires", table_name="agent_workflow_drafts")
    op.drop_table("agent_workflow_drafts")
