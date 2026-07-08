"""add batches.note column

Revision ID: a8e4f21c7d05
Revises: d3f8b1c62a95
Create Date: 2026-07-08 00:00:01.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a8e4f21c7d05"
down_revision: str | Sequence[str] | None = "d3f8b1c62a95"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """batches 加 note 欄：用戶上傳確認彈窗輸入的備註（每工作表一則，隨批次保存）。

    nullable：既有批次無備註，留 NULL 即可，無需回填。
    """
    op.add_column("batches", sa.Column("note", sa.Text(), nullable=True))


def downgrade() -> None:
    """回滾：移除 note 欄。"""
    op.drop_column("batches", "note")
