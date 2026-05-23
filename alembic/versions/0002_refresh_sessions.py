"""refresh_sessions

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-23
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import INET

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "refresh_sessions",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("family_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "parent_id",
            sa.BigInteger(),
            sa.ForeignKey("refresh_sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("used_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("user_agent", sa.String(255), nullable=True),
        sa.Column("ip", INET(), nullable=True),
    )
    op.create_index("ix_refresh_sessions_user_id", "refresh_sessions", ["user_id"])
    op.create_index("ix_refresh_sessions_family_id", "refresh_sessions", ["family_id"])
    op.create_index("ix_refresh_sessions_expires_at", "refresh_sessions", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_refresh_sessions_expires_at", table_name="refresh_sessions")
    op.drop_index("ix_refresh_sessions_family_id", table_name="refresh_sessions")
    op.drop_index("ix_refresh_sessions_user_id", table_name="refresh_sessions")
    op.drop_table("refresh_sessions")
