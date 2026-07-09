"""add judgments l2/l3/sentiment_score indexes

Revision ID: e1f7a2c95d38
Revises: d8b4e1f052a7
Create Date: 2026-07-10

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e1f7a2c95d38"
down_revision: str | Sequence[str] | None = "d8b4e1f052a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """補 judgments 的 l2_code / l3_code / sentiment_score btree 索引（原僅 l1_code 有）。

    taxonomy L2/L3 子樹篩選與情緒分篩選為列表熱路徑，先前這三欄全表掃；IF NOT EXISTS 冪等
    （dev 走 create_all 可能已建同名索引，避免衝突）。
    """
    op.execute("CREATE INDEX IF NOT EXISTS idx_judgments_l2 ON judgments (l2_code)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_judgments_l3 ON judgments (l3_code)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_judgments_sentiment ON judgments (sentiment_score)")


def downgrade() -> None:
    """移除三索引。"""
    op.execute("DROP INDEX IF EXISTS idx_judgments_sentiment")
    op.execute("DROP INDEX IF EXISTS idx_judgments_l3")
    op.execute("DROP INDEX IF EXISTS idx_judgments_l2")
