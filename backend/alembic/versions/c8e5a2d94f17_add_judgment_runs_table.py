"""add judgment_runs table (歸因歷史：每次批量/選取/單筆重判 run 一列)

Revision ID: c8e5a2d94f17
Revises: b7d2e4f1a9c3
Create Date: 2026-07-07

DDL 內嵌本檔（不 import tables.py）：該表其後更名為 prejudge_runs，live metadata 已無
judgment_runs 屬性，引用 T.judgment_runs 會使舊庫重放遷移鏈時 AttributeError；
本檔以「當時」的表結構固化（log 欄由 124748246b38 後續新增，不在此）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8e5a2d94f17"
down_revision: str | Sequence[str] | None = "b7d2e4f1a9c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """建歸因歷史表（run 級；has_table 守衛：dev 環境 app 啟動的 metadata.create_all
    可能已先建出此表，避開 DuplicateTable，等價原 checkfirst 語意）。"""
    bind = op.get_bind()
    if sa.inspect(bind).has_table("judgment_runs"):
        return
    op.create_table(
        "judgment_runs",
        sa.Column("job_id", sa.Text(), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("rejudge", sa.Boolean()),
        sa.Column("source", sa.Text()),
        sa.Column("model", sa.Text()),
        sa.Column("params", JSONB()),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("total", sa.Integer()),
        sa.Column("processed", sa.Integer()),
        sa.Column("ok", sa.Integer()),
        sa.Column("failed", sa.Integer()),
        sa.Column("total_tokens", sa.BigInteger()),
        sa.Column("cost_usd", sa.Float()),
        sa.Column("triggered_by", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index("idx_judgment_runs_started_at", "judgment_runs", ["started_at"])


def downgrade() -> None:
    """移除歸因歷史表（冪等）。"""
    op.execute("DROP TABLE IF EXISTS judgment_runs")
