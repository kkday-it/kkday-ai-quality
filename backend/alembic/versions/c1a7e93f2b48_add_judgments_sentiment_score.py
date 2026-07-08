"""add judgments.sentiment_score column

Revision ID: c1a7e93f2b48
Revises: b9f5d32e8a16
Create Date: 2026-07-08 00:00:03.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1a7e93f2b48"
down_revision: str | Sequence[str] | None = "b9f5d32e8a16"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """judgments 加 sentiment_score（LLM 讀原文判的情緒分 1-5，與外部評論 sentiment 同尺度）。

    nullable：既有判決無此值留 NULL，重判後回填；1-5 對應 負面1-2/中立3/正面4-5。
    """
    op.add_column("judgments", sa.Column("sentiment_score", sa.Integer(), nullable=True))


def downgrade() -> None:
    """回滾：移除 sentiment_score。"""
    op.drop_column("judgments", "sentiment_score")
