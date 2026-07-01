"""add judgments.source column

Revision ID: 31c690f0dd74
Revises: 3771110d1d2d
Create Date: 2026-07-01 00:00:01.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '31c690f0dd74'
down_revision: Union[str, Sequence[str], None] = '3771110d1d2d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """judgments 加 source 欄 + 索引，並一次性從 intake_items 回填既有列的 source。

    product_reviews 拆表後，判決結果需自知屬哪個來源才能正確 join 回對應表（intake_items
    或 product_reviews）。既有 judgments 列的 item_id 目前仍全數指向 intake_items（拆表 backfill
    腳本 scripts/backfill_product_reviews.py 尚未執行灌 product_reviews 表），故此處回填來源
    直接從 intake_items 取（一次性 UPDATE…FROM，非逐列 Python 迴圈，效率與正確性皆優）。
    """
    op.add_column("judgments", sa.Column("source", sa.Text(), nullable=True))
    op.create_index("idx_judgments_source", "judgments", ["source"], unique=False)
    op.execute(
        sa.text(
            "UPDATE judgments SET source = ii.source "
            "FROM intake_items ii "
            "WHERE judgments.item_id = ii.item_id"
        )
    )


def downgrade() -> None:
    """移除 judgments.source 欄與索引（回填值隨欄位一併消失，無法還原）。"""
    op.drop_index("idx_judgments_source", table_name="judgments")
    op.drop_column("judgments", "source")
