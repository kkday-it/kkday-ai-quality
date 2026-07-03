"""add judgments.item_id index

歸因查詢（list_problems / attribution_overview / attribution_breakdown / unjudged）皆以
judgments.item_id 與 intake_items / product_reviews outerjoin。缺此索引時對 ~8 萬列做
seq-scan / nested-loop，列表與縱覽載入緩慢。加 B-tree 索引消除 join 瓶頸。

Revision ID: a7f3c1d90b21
Revises: 663fbf45e97c
Create Date: 2026-07-02

"""

from collections.abc import Sequence

from alembic import op

revision: str = "a7f3c1d90b21"
down_revision: str | Sequence[str] | None = "663fbf45e97c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """建立 judgments.item_id 索引（IF NOT EXISTS 兼容已手動建過的環境）。"""
    op.execute("CREATE INDEX IF NOT EXISTS idx_judgments_item_id ON judgments (item_id)")


def downgrade() -> None:
    """移除 judgments.item_id 索引。"""
    op.execute("DROP INDEX IF EXISTS idx_judgments_item_id")
