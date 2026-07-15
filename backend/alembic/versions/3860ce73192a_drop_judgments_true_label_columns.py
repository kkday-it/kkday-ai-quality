"""drop judgments true_label columns (標真值功能整支退役)

Revision ID: 3860ce73192a
Revises: 6d4dfda55ce3
Create Date: 2026-07-14

標真值把關功能（人工標註歸因真值分類 + LLM 契合度評分）整支退役——inline 判官 prompt、
API 端點、前端 modal、離線監督準確率報表一併移除。judgments 的 5 個 true_label* 欄無寫入/查詢
路徑，drop 之。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3860ce73192a"
down_revision: str | Sequence[str] | None = "6d4dfda55ce3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLS = (
    "true_label",
    "true_label_reason",
    "true_label_conf",
    "true_label_updated_by",
    "true_label_updated_at",
)


def _existing(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    """移除 judgments 5 個 true_label* 欄（存在才刪，冪等）。"""
    have = _existing("judgments")
    for col in _COLS:
        if col in have:
            op.drop_column("judgments", col)


def downgrade() -> None:
    """加回 5 欄（nullable；true_label_conf 為 Float，餘 Text；資料不還原）。"""
    have = _existing("judgments")
    if "true_label" not in have:
        op.add_column("judgments", sa.Column("true_label", sa.Text(), nullable=True))
    if "true_label_reason" not in have:
        op.add_column("judgments", sa.Column("true_label_reason", sa.Text(), nullable=True))
    if "true_label_conf" not in have:
        op.add_column("judgments", sa.Column("true_label_conf", sa.Float(), nullable=True))
    if "true_label_updated_by" not in have:
        op.add_column("judgments", sa.Column("true_label_updated_by", sa.Text(), nullable=True))
    if "true_label_updated_at" not in have:
        op.add_column("judgments", sa.Column("true_label_updated_at", sa.Text(), nullable=True))
