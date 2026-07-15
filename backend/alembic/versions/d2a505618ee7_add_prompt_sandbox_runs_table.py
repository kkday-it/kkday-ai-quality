"""add prompt_sandbox_runs table

Revision ID: d2a505618ee7
Revises: 2751391347f7
Create Date: 2026-07-14 13:48:31.882949

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d2a505618ee7"
down_revision: str | Sequence[str] | None = "2751391347f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _prompt_sandbox_runs_table() -> sa.Table:
    metadata = sa.MetaData()
    return sa.Table(
        "prompt_sandbox_runs",
        metadata,
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("item_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("prompt_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("results", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("log", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("triggered_by", sa.Text(), nullable=True),
        sa.Column("job_id", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.PrimaryKeyConstraint("run_id"),
    )


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    table = _prompt_sandbox_runs_table()
    table.create(bind, checkfirst=True)
    sa.Index("idx_prompt_sandbox_runs_created", table.c.created_at).create(
        bind, checkfirst=True
    )


def downgrade() -> None:
    """Downgrade schema."""
    _prompt_sandbox_runs_table().drop(op.get_bind(), checkfirst=True)
