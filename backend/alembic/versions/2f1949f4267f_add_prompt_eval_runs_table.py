"""add prompt_eval_runs table (B2：Prompt 測試歷史)

Revision ID: 2f1949f4267f
Revises: bf198999cae4
Create Date: 2026-07-13

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2f1949f4267f"
down_revision: str | Sequence[str] | None = "bf198999cae4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _prompt_eval_runs_table() -> sa.Table:
    """保留 migration 当时的 schema，避免依赖会随业务演进而删除的当前 metadata。"""
    metadata = sa.MetaData()
    return sa.Table(
        "prompt_eval_runs",
        metadata,
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("prompt_id", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Integer(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("n", sa.Integer(), nullable=False),
        sa.Column("filters", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("mismatches", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("triggered_by", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("run_id", name="prompt_eval_runs_pkey"),
    )


def upgrade() -> None:
    """建 Prompt 測試歷史表（checkfirst：dev 環境 app 啟動的 metadata.create_all 可能已先建出
    此表，避開 DuplicateTable，仿 f2a8c4d61e93/c8e5a2d94f17 既有慣例）。"""
    bind = op.get_bind()
    table = _prompt_eval_runs_table()
    table.create(bind, checkfirst=True)
    sa.Index(
        "idx_prompt_eval_runs_prompt_created",
        table.c.prompt_id,
        table.c.created_at,
    ).create(bind, checkfirst=True)


def downgrade() -> None:
    """移除 Prompt 測試歷史表。"""
    bind = op.get_bind()
    _prompt_eval_runs_table().drop(bind, checkfirst=True)
