"""add prompt_testcases table (B3：邊界測試集)

Revision ID: 581ca1e72296
Revises: 2f1949f4267f
Create Date: 2026-07-14

"""

from collections.abc import Sequence

from alembic import op
from app.core.db import tables as T

# revision identifiers, used by Alembic.
revision: str = "581ca1e72296"
down_revision: str | Sequence[str] | None = "2f1949f4267f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """建邊界測試集表（checkfirst：dev 環境 app 啟動的 metadata.create_all 可能已先建出此表）。"""
    bind = op.get_bind()
    T.prompt_testcases.create(bind, checkfirst=True)


def downgrade() -> None:
    """移除邊界測試集表。"""
    bind = op.get_bind()
    T.prompt_testcases.drop(bind, checkfirst=True)
