"""drop judgments.dimension column (退役 legacy 8 面向平行分類)

Revision ID: 6d4dfda55ce3
Revises: 581ca1e72296
Create Date: 2026-07-14

judgments.dimension 為舊「8 大內容治理維度」相容欄，由 prejudge 的 _CONTENT_DIM_KEYWORDS
中文關鍵詞硬編碼分類寫入（與 docs/prompts/prompts 的 facet_catalog 平行、且已 drift），前端零消費。
隨 Prompt-as-Source 收斂一併退役——分類真訊號在 l1_code/l2_code。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6d4dfda55ce3"
down_revision: str | Sequence[str] | None = "581ca1e72296"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(table: str, column: str) -> bool:
    """欄位是否存在（dev create_all 於本 migration 後不再建此欄，故 drop 前先探，冪等避開 UndefinedColumn）。"""
    insp = sa.inspect(op.get_bind())
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    """移除 judgments.dimension 欄（存在才刪）。"""
    if _has_column("judgments", "dimension"):
        op.drop_column("judgments", "dimension")


def downgrade() -> None:
    """加回 dimension 欄（nullable；資料不還原）。"""
    if not _has_column("judgments", "dimension"):
        op.add_column("judgments", sa.Column("dimension", sa.Text(), nullable=True))
