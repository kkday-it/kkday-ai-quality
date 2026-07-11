"""add judgment_history snapshot partial index（多模型快照查詢熱路徑）

Revision ID: b5c7e91f3a26
Revises: a3b9d5e72f04
Create Date: 2026-07-11

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b5c7e91f3a26"
down_revision: str | Sequence[str] | None = "a3b9d5e72f04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """`latest_snapshots`（DISTINCT ON）專用 partial index。

    (source, model, source_id, created_at DESC) WHERE kind='judgment'：讓每評論取指定模型
    最新快照的查詢直接吃索引序、免額外 Sort；現量級（萬筆內）無此索引也是毫秒級，
    此為隨歷史列數成長的前瞻強化。IF NOT EXISTS 冪等（dev create_all 不建 DDL 外索引，
    但防重跑）。
    """
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_judgment_history_snapshot "
        "ON judgment_history (source, model, source_id, created_at DESC) "
        "WHERE kind = 'judgment'"
    )


def downgrade() -> None:
    """移除快照查詢索引。"""
    op.execute("DROP INDEX IF EXISTS idx_judgment_history_snapshot")
