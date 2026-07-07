"""judgments 加 model_votes JSONB 欄（多 model 聯合判決 ensemble 各 voter 攤平票）

Revision ID: b7d2e4f1a9c3
Revises: a3f1c9d2b7e4
Create Date: 2026-07-06

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7d2e4f1a9c3"
down_revision: str | Sequence[str] | None = "a3f1c9d2b7e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """judgments.model_votes：ensemble 聯合判決時存各 voter 的 [{model,l1_code,l2_code,l3_code,conf}]。

    單模型判決為 NULL；供比較報告（每 voter vs true_label、Cohen's κ 一致性）與前端展開票用。
    dev app reload 的 create_all 可能已先建欄 → IF NOT EXISTS 冪等加欄（比照 checkfirst 精神）。
    """
    op.execute("ALTER TABLE judgments ADD COLUMN IF NOT EXISTS model_votes jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE judgments DROP COLUMN IF EXISTS model_votes")
