"""drop roster quality calibration tables

Revision ID: 663fbf45e97c
Revises: 31c690f0dd74
Create Date: 2026-07-02 11:11:00.376611

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "663fbf45e97c"
down_revision: str | Sequence[str] | None = "31c690f0dd74"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# roster 主檔 / 質檢彙總 / 進線 / 校準 — 功能永久下線，只保留來源數據
_TABLES = [
    "prod_quality",
    "pkg_quality",
    "inquiries",
    "orders",
    "packages",
    "suppliers",
    "products",
    "confidence_calibration",
]


def upgrade() -> None:
    """移除 8 個未用表（roster / 質檢彙總 / 進線 / 校準）。

    這些表原多由 metadata.create_all 產生（roster 從無 create migration）、confidence_calibration
    於 baseline 建立；統一以 DROP TABLE IF EXISTS ... CASCADE 冪等移除（相依 index 隨表一併刪、
    不同環境是否存在皆安全）。
    """
    for table in _TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")


def downgrade() -> None:
    """不可逆：本 migration 為功能永久下線；來源數據不含這些表，如需回復請由歷史定義重建。"""
    raise NotImplementedError("roster/質檢彙總/校準 表已永久移除，不支援 downgrade 重建")
