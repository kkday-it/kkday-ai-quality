"""drop product_reviews judges columns (1:N pivot to judgments)

Revision ID: 8ff0b8cfc8f2
Revises: 7f1daa86d0a8
Create Date: 2026-07-03 16:19:55.848932

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8ff0b8cfc8f2"
down_revision: str | Sequence[str] | None = "7f1daa86d0a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """移除 product_reviews 判決三欄——多歸因改採 1:N（每條歸因＝一筆獨立 judgments 列，
    判決結果全部統一存 judgments 表），先前 array-embed 於 product_reviews 自帶欄的方案作廢。"""
    op.drop_column("product_reviews", "judged_at")
    op.drop_column("product_reviews", "review_polarity")
    op.drop_column("product_reviews", "judges")


def downgrade() -> None:
    """還原判決三欄（judges JSONB NOT NULL DEFAULT '[]' + review_polarity + judged_at）。"""
    op.add_column(
        "product_reviews",
        sa.Column(
            "judges",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column("product_reviews", sa.Column("review_polarity", sa.Text(), nullable=True))
    op.add_column("product_reviews", sa.Column("judged_at", sa.Text(), nullable=True))
