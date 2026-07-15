"""add judgment_runs log column (LLM 執行日誌落庫回看)

Revision ID: 124748246b38
Revises: d995b5aee8f4
Create Date: 2026-07-15

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "124748246b38"
down_revision: str | Sequence[str] | None = "d995b5aee8f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """judgment_runs 加 log 欄（JSONB，nullable）：落存 run_log.read(job_id) 快照，仿
    prompt_sandbox_runs.log 同一模式，供判決歷史「查看 LLM 日誌」入口事後回看完整執行日誌
    （僅小批量 job 有收集內容；大批量/舊資料為 NULL）。"""
    op.add_column("judgment_runs", sa.Column("log", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    """移除 log 欄。"""
    op.drop_column("judgment_runs", "log")
