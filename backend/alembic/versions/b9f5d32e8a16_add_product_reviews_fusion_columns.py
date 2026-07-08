"""add product_reviews fusion columns (review_external_lst_oid / sentiment / free_tag)

Revision ID: b9f5d32e8a16
Revises: a8e4f21c7d05
Create Date: 2026-07-08 00:00:02.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b9f5d32e8a16"
down_revision: str | Sequence[str] | None = "a8e4f21c7d05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """product_reviews 加評論系統融合三欄（19 欄導出新增欄，rec_oid 對橋）。

    皆 nullable：舊列無融合資料留 NULL；重新上傳同檔即以 rec_oid upsert 回填。
    sentiment/free_tag 為輔助訊號（傾向/歸因以評論原文 LLM 判定為準）。
    """
    op.add_column("product_reviews", sa.Column("review_external_lst_oid", sa.Text(), nullable=True))
    op.add_column("product_reviews", sa.Column("sentiment", sa.Text(), nullable=True))
    op.add_column("product_reviews", sa.Column("free_tag", sa.Text(), nullable=True))


def downgrade() -> None:
    """回滾：移除融合三欄。"""
    op.drop_column("product_reviews", "free_tag")
    op.drop_column("product_reviews", "sentiment")
    op.drop_column("product_reviews", "review_external_lst_oid")
