"""rename domain machine values（product_quality→quality、redemption→platform）

Revision ID: bf198999cae4
Revises: b5c7e91f3a26
Create Date: 2026-07-13

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "bf198999cae4"
down_revision: str | Sequence[str] | None = "b5c7e91f3a26"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 對齊 Prompt-as-Source 架構：域機器值改由 prompt 檔名尾綴推導（見 prompt_source.py），
# 04_C-4_platform.md / 02_C-2_quality.md 檔名即新域值——樹裡舊值 redemption/product_quality
# 需同步遷移，否則 judgments.l1_code 與新引擎輸出的域值對不上（歸因列表級聯篩選/概覽圖表失準）。
_RENAME = {"product_quality": "quality", "redemption": "platform"}


def upgrade() -> None:
    """judgments.l1_code 依 _RENAME 換值。

    僅動 l1_code：true_label 現存值為 C-code（如 C-2-2-2，非裸域字串）不受影響；
    judgment_history.attributions / judgments.model_votes 為歷史快照 JSONB，依既有慣例
    （2026-07-13 L2 代碼連續化遷移時已確立）不回填——歷史解讀看 label 非 code。
    """
    for old, new in _RENAME.items():
        op.execute(f"UPDATE judgments SET l1_code = '{new}' WHERE l1_code = '{old}'")


def downgrade() -> None:
    """還原（值遷移可逆：無其他寫入路徑會產生新值時的中間態，直接反向 UPDATE 安全）。"""
    for old, new in _RENAME.items():
        op.execute(f"UPDATE judgments SET l1_code = '{old}' WHERE l1_code = '{new}'")
