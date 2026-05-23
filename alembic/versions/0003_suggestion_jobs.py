"""suggestion_jobs queue table

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-23
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SUGGESTION_STATUS = postgresql.ENUM(
    "queued", "running", "done", "error",
    name="suggestion_status",
    create_type=False,
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute(
        "CREATE TYPE suggestion_status AS ENUM "
        "('queued', 'running', 'done', 'error')"
    )

    op.create_table(
        "suggestion_jobs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "status",
            SUGGESTION_STATUS,
            nullable=False,
            server_default="queued",
        ),
        sa.Column("jd_text", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    op.create_index(
        "suggestion_jobs_one_active_per_user",
        "suggestion_jobs",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_index(
        "suggestion_jobs_user_created_idx",
        "suggestion_jobs",
        ["user_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "suggestion_jobs_recovery_idx",
        "suggestion_jobs",
        ["status", "started_at"],
        postgresql_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    op.drop_index("suggestion_jobs_recovery_idx", table_name="suggestion_jobs")
    op.drop_index("suggestion_jobs_user_created_idx", table_name="suggestion_jobs")
    op.drop_index(
        "suggestion_jobs_one_active_per_user", table_name="suggestion_jobs",
    )
    op.drop_table("suggestion_jobs")
    op.execute("DROP TYPE IF EXISTS suggestion_status")
