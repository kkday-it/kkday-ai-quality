"""drop conversations product_category column

Revision ID: 9cb90ab176c4
Revises: 7f55f054c284
Create Date: 2026-07-27 11:31:38.766938

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9cb90ab176c4"
down_revision: str | Sequence[str] | None = "7f55f054c284"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """退役 conversations.product_category（全棧清退，見 bd_tag_vertical 分類系統取代）。冪等 IF EXISTS。"""
    op.execute("DROP INDEX IF EXISTS idx_conversations_product_category")
    op.execute("ALTER TABLE conversations DROP COLUMN IF EXISTS product_category")


def downgrade() -> None:
    """還原欄位結構（nullable，不還原資料——資料已隨欄位刪除）。"""
    op.add_column(
        "conversations",
        sa.Column("product_category", sa.TEXT(), autoincrement=False, nullable=True),
    )
    op.create_index(
        op.f("idx_conversations_product_category"),
        "conversations",
        ["product_category"],
        unique=False,
    )
