"""drop verdict axis single-axis convergence

Revision ID: c7ae2e2be254
Revises: df57def04797
Create Date: 2026-07-01 14:23:42.884748

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7ae2e2be254"
down_revision: str | Sequence[str] | None = "df57def04797"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# 8 面向 code（prod_quality / pkg_quality 各有一組 {code}_verdict 欄）
_FACET_CODES: tuple[str, ...] = (
    "fee",
    "group_form",
    "itinerary",
    "meetup",
    "positioning",
    "redeem",
    "restriction",
    "sla",
)
_FACET_TABLES: tuple[str, ...] = ("prod_quality", "pkg_quality")


def upgrade() -> None:
    """移除 verdict 軸（軸B）：系統收斂為單軸 polarity + L1→L3 歸因。

    刪 judgments.verdict（2022 筆歷史值，已 pg_dump 備份）與 prod/pkg_quality 各 8 個
    {code}_verdict 面向欄。此為破壞性 DDL，資料不可回復（downgrade 僅重建空欄結構）。
    """
    op.drop_column("judgments", "verdict")
    for table in _FACET_TABLES:
        for code in _FACET_CODES:
            op.drop_column(table, f"{code}_verdict")


def downgrade() -> None:
    """重建欄位結構（僅結構，歷史 verdict 值無法還原；如需資料回灌用備份 SQL）。"""
    op.add_column("judgments", sa.Column("verdict", sa.Text(), nullable=True))
    for table in _FACET_TABLES:
        for code in _FACET_CODES:
            op.add_column(table, sa.Column(f"{code}_verdict", sa.Text(), nullable=True))
