"""add llm_usage table (per-call AI usage log)

Revision ID: e6b3c81f9a24
Revises: d5a2f9c34b16
Create Date: 2026-07-06

"""

from collections.abc import Sequence

from alembic import op
from app.core.db import tables as T

# revision identifiers, used by Alembic.
revision: str = "e6b3c81f9a24"
down_revision: str | Sequence[str] | None = "d5a2f9c34b16"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """建 AI 使用紀錄表（per-call：每次真 LLM 呼叫落一列，供消耗 dashboard 聚合）。

    用 `T.llm_usage.create(bind, checkfirst=True)`（非 op.create_table）：dev 環境 app 啟動的
    metadata.create_all 可能已先建出此表，checkfirst 避開 DuplicateTable（仿 648f09878b62 前例）。
    """
    bind = op.get_bind()
    T.llm_usage.create(bind, checkfirst=True)


def downgrade() -> None:
    """移除 AI 使用紀錄表。"""
    bind = op.get_bind()
    T.llm_usage.drop(bind, checkfirst=True)
