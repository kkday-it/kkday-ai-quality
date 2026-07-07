"""add true_label audit columns (reason + llm confidence)

Revision ID: c4f1a7d20e83
Revises: b3e9c2a10f47
Create Date: 2026-07-06

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4f1a7d20e83"
down_revision: str | Sequence[str] | None = "b3e9c2a10f47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """judgments 加標真值把關 audit 兩欄（皆 nullable，NULL＝未標/未評）。

    true_label_reason：LLM 對人工真值信心明顯下降時人工填的修改理由（防亂標·留痕可追）。
    true_label_conf：標真值當下 LLM 對該真值的契合信心（0~1；audit + 準確率評估）。
    兩欄與既有 true_label 同屬人工標註軸，重判（replace_source_findings）依 finding_id 一併保留。
    """
    op.add_column("judgments", sa.Column("true_label_reason", sa.Text(), nullable=True))
    op.add_column("judgments", sa.Column("true_label_conf", sa.Float(), nullable=True))


def downgrade() -> None:
    """移除標真值把關 audit 兩欄。"""
    op.drop_column("judgments", "true_label_conf")
    op.drop_column("judgments", "true_label_reason")
