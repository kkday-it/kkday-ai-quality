"""drop intake_items and backup tables

Revision ID: 209f902fd979
Revises: 648f09878b62
Create Date: 2026-07-04 09:19:22.875169

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "209f902fd979"
down_revision: str | Sequence[str] | None = "648f09878b62"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """清理殘留表：

    - intake_items：5 來源全拆表後已空(0 列)，通用 fallback 邏輯亦移除 → drop。
    - 3 個手動 backup 表：遷移驗證後留存的安全網，資料已冗餘於 live 表 → drop。
      （全庫備份 backend/backups/kkdb_ai_quality_pre_cleanup_20260704.sql 為最終安全網。）
    """
    op.execute("DROP TABLE IF EXISTS intake_items")
    op.execute("DROP TABLE IF EXISTS intake_items_backup_pr_residual")
    op.execute("DROP TABLE IF EXISTS judgments_backup_pre_1n_20260703")
    op.execute("DROP TABLE IF EXISTS judge_rule_versions_backup_20260703")


def downgrade() -> None:
    """僅重建 intake_items 空表結構（backup 表為一次性快照，資料不可逆，不重建）。"""
    op.create_table(
        "intake_items",
        sa.Column("item_id", sa.Text(), primary_key=True),
        sa.Column("source", sa.Text()),
        sa.Column("batch_id", sa.Text()),
        sa.Column("prod_oid", sa.Text()),
        sa.Column("pkg_oid", sa.Text()),
        sa.Column("rating", sa.Integer()),
        sa.Column("comment", sa.Text()),
        sa.Column("raw", sa.Text()),
        sa.Column("status", sa.Text()),
        sa.Column("created_at", sa.Text()),
        sa.Column("occurred_at", sa.Text()),
    )
