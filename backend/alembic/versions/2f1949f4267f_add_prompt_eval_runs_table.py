"""add prompt_eval_runs table (B2：Prompt 測試歷史)

Revision ID: 2f1949f4267f
Revises: bf198999cae4
Create Date: 2026-07-13

"""

from collections.abc import Sequence

from alembic import op
from app.core.db import tables as T

# revision identifiers, used by Alembic.
revision: str = "2f1949f4267f"
down_revision: str | Sequence[str] | None = "bf198999cae4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """建 Prompt 測試歷史表（checkfirst：dev 環境 app 啟動的 metadata.create_all 可能已先建出
    此表，避開 DuplicateTable，仿 f2a8c4d61e93/c8e5a2d94f17 既有慣例）。"""
    bind = op.get_bind()
    T.prompt_eval_runs.create(bind, checkfirst=True)


def downgrade() -> None:
    """移除 Prompt 測試歷史表。"""
    bind = op.get_bind()
    T.prompt_eval_runs.drop(bind, checkfirst=True)
