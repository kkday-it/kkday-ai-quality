"""add judgment_runs table (歸因歷史：每次批量/選取/單筆重判 run 一列)

Revision ID: c8e5a2d94f17
Revises: b7d2e4f1a9c3
Create Date: 2026-07-07

"""

from collections.abc import Sequence

from alembic import op
from app.core.db import tables as T

# revision identifiers, used by Alembic.
revision: str = "c8e5a2d94f17"
down_revision: str | Sequence[str] | None = "b7d2e4f1a9c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """建歸因歷史表（run 級：與 llm_usage 以 job_id 關聯，per-stage 明細由 llm_usage 聚合）。

    用 `T.judgment_runs.create(bind, checkfirst=True)`（非 op.create_table）：dev 環境 app 啟動的
    metadata.create_all 可能已先建出此表，checkfirst 避開 DuplicateTable（仿 e6b3c81f9a24 前例）。
    """
    bind = op.get_bind()
    T.judgment_runs.create(bind, checkfirst=True)


def downgrade() -> None:
    """移除歸因歷史表。"""
    bind = op.get_bind()
    T.judgment_runs.drop(bind, checkfirst=True)
