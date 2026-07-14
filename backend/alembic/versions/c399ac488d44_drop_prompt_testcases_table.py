"""drop prompt_testcases table

Revision ID: c399ac488d44
Revises: 3860ce73192a
Create Date: 2026-07-14 06:10:23.120687

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c399ac488d44"
down_revision: str | Sequence[str] | None = "3860ce73192a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """移除 prompt_testcases 表（B3 mock 邊界測試集整支退役）。

    mock 上傳 / 手動新增 / 分歧一鍵入集功能已全數移除（UI + API + 資料層），本表無新寫入路徑亦無
    查詢消費；以 DROP TABLE IF EXISTS ... CASCADE 冪等移除（相依 index 隨表一併刪，環境是否存在皆安全）。
    """
    op.execute("DROP TABLE IF EXISTS prompt_testcases CASCADE")


def downgrade() -> None:
    """重建表結構（空表，不還原資料）——比照原 `tables.prompt_testcases` DDL。"""
    op.create_table(
        "prompt_testcases",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("gold_l1", sa.Text(), nullable=False),
        sa.Column("gold_l2", sa.Text()),
        sa.Column("expected_polarity", sa.Text()),
        sa.Column("note", sa.Text()),
        sa.Column("tags", JSONB()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_by", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_prompt_testcases_gold_l1", "prompt_testcases", ["gold_l1"])
