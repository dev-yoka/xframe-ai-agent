"""add conversation kind

Revision ID: 202605210001
Revises: 202605200001
Create Date: 2026-05-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202605210001"
down_revision: str | None = "202605200001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_conversations",
        sa.Column("kind", sa.String(length=64), nullable=True, server_default="general"),
    )


def downgrade() -> None:
    op.drop_column("agent_conversations", "kind")
