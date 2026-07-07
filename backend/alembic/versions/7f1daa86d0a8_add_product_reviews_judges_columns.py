"""add product_reviews judges columns

Revision ID: 7f1daa86d0a8
Revises: a7f3c1d90b21
Create Date: 2026-07-03 14:33:40.663159

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7f1daa86d0a8"
down_revision: str | Sequence[str] | None = "a7f3c1d90b21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """product_reviews 加多歸因判決三欄（judges/review_polarity/judged_at）。

    judges NOT NULL DEFAULT '[]'::jsonb：PG17 常數 default 為 metadata-only（免全表 rewrite），
    3.7 萬列規模零鎖表風險。review_polarity/judged_at 為 nullable（NULL＝未判）。
    判決由 db.upsert_review_judges 獨立寫入，ingest 路徑不碰（見 db._PR_JUDGE_COLS）。
    """
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


def downgrade() -> None:
    """移除多歸因判決三欄。"""
    op.drop_column("product_reviews", "judged_at")
    op.drop_column("product_reviews", "review_polarity")
    op.drop_column("product_reviews", "judges")
