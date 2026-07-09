"""add judgments manual audit columns (status/true_label updated_by+at)

Revision ID: d8b4e1f052a7
Revises: c1a7e93f2b48
Create Date: 2026-07-09

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d8b4e1f052a7"
down_revision: str | Sequence[str] | None = "c1a7e93f2b48"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """judgments 加人工覆核 audit 四欄（皆 nullable；系統自動路由不寫，僅人工操作留痕）。

    status_updated_by / status_updated_at：誰、何時人工改了 status（confirmed/dismissed/fixed）。
    true_label_updated_by / true_label_updated_at：誰、何時標/清了 true_label。
    補上原本 UPDATE 端點（findings.patch_*）不記操作者/時間的缺口——與既有 true_label_reason/conf
    （LLM 把關 audit）互補，記錄操作者身分。時間欄沿用 ISO 字串（Text，與 created_at 同形態）。
    """
    op.add_column("judgments", sa.Column("status_updated_by", sa.Text(), nullable=True))
    op.add_column("judgments", sa.Column("status_updated_at", sa.Text(), nullable=True))
    op.add_column("judgments", sa.Column("true_label_updated_by", sa.Text(), nullable=True))
    op.add_column("judgments", sa.Column("true_label_updated_at", sa.Text(), nullable=True))


def downgrade() -> None:
    """移除人工覆核 audit 四欄。"""
    op.drop_column("judgments", "true_label_updated_at")
    op.drop_column("judgments", "true_label_updated_by")
    op.drop_column("judgments", "status_updated_at")
    op.drop_column("judgments", "status_updated_by")
