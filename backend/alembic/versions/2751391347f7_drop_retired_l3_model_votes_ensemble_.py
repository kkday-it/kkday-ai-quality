"""drop retired l3 model_votes ensemble_voters columns

Revision ID: 2751391347f7
Revises: c399ac488d44
Create Date: 2026-07-14 07:07:32.167642

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2751391347f7"
down_revision: str | Sequence[str] | None = "c399ac488d44"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """移除退役殘留欄（L3 深度 + 跨廠 ensemble）。

    L3 判準層與跨廠 ensemble 皆已退役，下列欄位恆空（l3_code/l3_label 空字串、model_votes NULL、
    ensemble_voters 0），無寫入路徑亦無查詢消費。以 DROP COLUMN IF EXISTS 冪等移除
    （相依索引 idx_judgments_l3 隨欄一併刪）。
    """
    op.execute("ALTER TABLE judgments DROP COLUMN IF EXISTS l3_code")
    op.execute("ALTER TABLE judgments DROP COLUMN IF EXISTS l3_label")
    op.execute("ALTER TABLE judgments DROP COLUMN IF EXISTS model_votes")
    op.execute("ALTER TABLE judgment_history DROP COLUMN IF EXISTS model_votes")
    op.execute("ALTER TABLE judgment_runs DROP COLUMN IF EXISTS ensemble_voters")


def downgrade() -> None:
    """加回欄位（nullable，資料不還原）。"""
    op.add_column("judgments", sa.Column("l3_code", sa.Text()))
    op.add_column("judgments", sa.Column("l3_label", sa.Text()))
    op.add_column("judgments", sa.Column("model_votes", JSONB()))
    op.create_index("idx_judgments_l3", "judgments", ["l3_code"])
    op.add_column("judgment_history", sa.Column("model_votes", JSONB()))
    op.add_column("judgment_runs", sa.Column("ensemble_voters", sa.Integer()))
