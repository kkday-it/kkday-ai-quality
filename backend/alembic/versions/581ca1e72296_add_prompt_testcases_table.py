"""add prompt_testcases table (B3：邊界測試集)

Revision ID: 581ca1e72296
Revises: 2f1949f4267f
Create Date: 2026-07-14

DDL 內嵌本檔（不 import tables.py）：該表已於 c399ac488d44 退役、Table 定義已自
tables.py 移除，引用 T.prompt_testcases 會使舊庫重放遷移鏈時 AttributeError。
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


def upgrade() -> None:
    """建邊界測試集表（has_table 守衛：dev 環境 app 啟動的 metadata.create_all
    可能已先建出此表，避開 DuplicateTable，等價原 checkfirst 語意）。"""
    bind = op.get_bind()
    if sa.inspect(bind).has_table("prompt_testcases"):
        return
    op.create_table(
        "prompt_testcases",
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
    op.create_index("idx_prompt_testcases_gold_l1", "prompt_testcases", ["gold_l1"])


def downgrade() -> None:
    """移除邊界測試集表（冪等）。"""
    op.execute("DROP TABLE IF EXISTS prompt_testcases")
