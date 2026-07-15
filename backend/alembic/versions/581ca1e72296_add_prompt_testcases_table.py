"""add prompt_testcases table (B3：邊界測試集)

Revision ID: 581ca1e72296
Revises: 2f1949f4267f
Create Date: 2026-07-14

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "581ca1e72296"
down_revision: str | Sequence[str] | None = "2f1949f4267f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _prompt_testcases_table() -> sa.Table:
    """保留 migration 当时的 schema，避免依赖会随业务演进而删除的当前 metadata。"""
    metadata = sa.MetaData()
    return sa.Table(
        "prompt_testcases",
        metadata,
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("gold_l1", sa.Text(), nullable=False),
        sa.Column("gold_l2", sa.Text()),
        sa.Column("expected_polarity", sa.Text()),
        sa.Column("note", sa.Text()),
        sa.Column("tags", JSONB()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_by", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def upgrade() -> None:
    """建邊界測試集表（checkfirst：dev 環境 app 啟動的 metadata.create_all 可能已先建出此表）。"""
    bind = op.get_bind()
    table = _prompt_testcases_table()
    table.create(bind, checkfirst=True)
    sa.Index("idx_prompt_testcases_gold_l1", table.c.gold_l1).create(bind, checkfirst=True)


def downgrade() -> None:
    """移除邊界測試集表。"""
    bind = op.get_bind()
    _prompt_testcases_table().drop(bind, checkfirst=True)
