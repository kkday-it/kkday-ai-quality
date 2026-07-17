"""add prompt_eval_runs table (B2：Prompt 測試歷史)

Revision ID: 2f1949f4267f
Revises: bf198999cae4
Create Date: 2026-07-13

DDL 內嵌本檔（不 import tables.py）：該表已於 d995b5aee8f4 退役、Table 定義已自
tables.py 移除，引用 T.prompt_eval_runs 會使舊庫重放遷移鏈時 AttributeError。
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


def upgrade() -> None:
    """建 Prompt 測試歷史表（has_table 守衛：dev 環境 app 啟動的 metadata.create_all
    可能已先建出此表，避開 DuplicateTable，等價原 checkfirst 語意）。"""
    bind = op.get_bind()
    if sa.inspect(bind).has_table("prompt_eval_runs"):
        return
    op.create_table(
        "prompt_eval_runs",
        sa.Column("run_id", sa.TEXT(), nullable=False),
        sa.Column("prompt_id", sa.TEXT(), nullable=False),
        sa.Column("prompt_version", sa.INTEGER(), nullable=True),
        sa.Column("source", sa.TEXT(), nullable=False),
        sa.Column("n", sa.INTEGER(), nullable=False),
        sa.Column("filters", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("mismatches", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("model", sa.TEXT(), nullable=True),
        sa.Column("triggered_by", sa.TEXT(), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("run_id", name="prompt_eval_runs_pkey"),
    )
    op.create_index(
        "idx_prompt_eval_runs_prompt_created",
        "prompt_eval_runs",
        ["prompt_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """移除 Prompt 測試歷史表（冪等）。"""
    op.execute("DROP TABLE IF EXISTS prompt_eval_runs")
