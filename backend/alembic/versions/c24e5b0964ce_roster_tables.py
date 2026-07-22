"""roster tables

Revision ID: c24e5b0964ce
Revises: bd77052f7222
Create Date: 2026-06-30 13:26:04.366693

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c24e5b0964ce"
down_revision: str | Sequence[str] | None = "bd77052f7222"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema。

    DROP 用 IF EXISTS：既有庫此索引為 DESC 排序版（重建成 ASC），從零重放的鏈上
    該索引不存在——裸 drop 會炸，冪等化兩者皆容。
    """
    op.execute("DROP INDEX IF EXISTS idx_prod_quality_issue")
    op.create_index("idx_prod_quality_issue", "prod_quality", ["content_issue_n"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP INDEX IF EXISTS idx_prod_quality_issue")
    op.create_index(
        op.f("idx_prod_quality_issue"),
        "prod_quality",
        [sa.literal_column("content_issue_n DESC")],
        unique=False,
    )
