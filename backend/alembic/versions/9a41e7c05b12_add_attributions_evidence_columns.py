"""add attributions evidence columns

Revision ID: 9a41e7c05b12
Revises: 2c8ed24edb24
Create Date: 2026-07-22

訂單佐證閉環（feat/order-evidence-loop）：attributions 新增判決佐證留痕三欄——

- evidence_status：佐證取數結果（fetched/cache_hit/no_order_oid/not_found/
  degraded_unavailable/error；NULL＝未走佐證流程的舊資料）
- evidence_citation：注入 prompt 的佐證摘要文字（白名單欄位拼接；稽核 C-1/C-6 分流用）
- evidence_fetched_at：佐證取數時刻（ISO 字串；人工複核判斷資料新鮮度）

冪等（IF NOT EXISTS）：dev 空庫走 create_all+stamp 不經此檔；僅既有庫升級執行。
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9a41e7c05b12"
down_revision: str | Sequence[str] | None = "2c8ed24edb24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = ("evidence_status", "evidence_citation", "evidence_fetched_at")


def upgrade() -> None:
    """attributions 加三欄（TEXT NULLable；舊列 NULL＝未走佐證流程）。"""
    for col in _COLUMNS:
        op.execute(f"ALTER TABLE attributions ADD COLUMN IF NOT EXISTS {col} TEXT")


def downgrade() -> None:
    """回滾：移除三欄。"""
    for col in _COLUMNS:
        op.execute(f"ALTER TABLE attributions DROP COLUMN IF EXISTS {col}")
