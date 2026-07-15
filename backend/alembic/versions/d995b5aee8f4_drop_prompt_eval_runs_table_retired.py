"""drop prompt_eval_runs table (retired)

Revision ID: d995b5aee8f4
Revises: d2a505618ee7
Create Date: 2026-07-15 02:14:25.474832

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d995b5aee8f4"
down_revision: str | Sequence[str] | None = "d2a505618ee7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_index(op.f("idx_prompt_eval_runs_prompt_created"), table_name="prompt_eval_runs")
    op.drop_table("prompt_eval_runs")


def downgrade() -> None:
    """Downgrade schema."""
    op.create_table(
        "prompt_eval_runs",
        sa.Column("run_id", sa.TEXT(), autoincrement=False, nullable=False),
        sa.Column("prompt_id", sa.TEXT(), autoincrement=False, nullable=False),
        sa.Column("prompt_version", sa.INTEGER(), autoincrement=False, nullable=True),
        sa.Column("source", sa.TEXT(), autoincrement=False, nullable=False),
        sa.Column("n", sa.INTEGER(), autoincrement=False, nullable=False),
        sa.Column(
            "filters", postgresql.JSONB(astext_type=sa.Text()), autoincrement=False, nullable=True
        ),
        sa.Column(
            "metrics", postgresql.JSONB(astext_type=sa.Text()), autoincrement=False, nullable=False
        ),
        sa.Column(
            "mismatches",
            postgresql.JSONB(astext_type=sa.Text()),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column("model", sa.TEXT(), autoincrement=False, nullable=True),
        sa.Column("triggered_by", sa.TEXT(), autoincrement=False, nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("run_id", name=op.f("prompt_eval_runs_pkey")),
    )
    op.create_index(
        op.f("idx_prompt_eval_runs_prompt_created"),
        "prompt_eval_runs",
        ["prompt_id", "created_at"],
        unique=False,
    )
