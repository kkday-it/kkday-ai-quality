"""theme 受控值升版：碼名間加空格、跳出分支 OOT跳出 → 其他

Revision ID: c4e81b7a35d2
Revises: b7c41e9d2a53
Create Date: 2026-07-28 15:45:00.000000

2026-07-28 起 L1 theme 的受控值改成裁判表寫法（`[119] 單據/發票`、跳出為 `其他`），
schema enum 與 Prompt 快照同批升版。案例庫存的是**當時判定與人工正解的逐字值**，
不跟著遷就會在回歸重跑時把舊案例判成「還是不對」——比對的是字串，差一個空格就不相等。

只動 `prompt_debug_reviews` 的兩個 JSONB 欄位：`ai_output.theme`（AI 當時判的）與
`corrections.theme`（人工填的正解）。`confirmed` 存的是欄位鍵名不是值，不受影響。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4e81b7a35d2"
down_revision: str | Sequence[str] | None = "b7c41e9d2a53"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 舊值 → 新值。一對一且雙向唯一，故 downgrade 直接反轉即可。
_THEME_RENAMES: list[tuple[str, str]] = [
    ("[104]訂單確認問題", "[104] 訂單確認問題"),
    ("[101]訂單取消", "[101] 訂單取消"),
    ("[93]訂單申請修改", "[93] 訂單申請修改"),
    ("[COMM]連線通訊商品使用問題", "[COMM] 連線通訊商品使用問題"),
    ("[119]單據/發票", "[119] 單據/發票"),
    ("OOT跳出", "其他"),
]

# 只在該欄現值等於舊值時才寫入：jsonb_set 預設 create_missing=true，沒有 WHERE 會給
# 原本沒有 theme 鍵的列硬塞一個出來（corrections 常常只存人工改過的那幾欄）。
_SQL = sa.text(
    """
    UPDATE prompt_debug_reviews
       SET {column} = jsonb_set({column}, '{{theme}}', to_jsonb(CAST(:new AS text)))
     WHERE {column}->>'theme' = :old
    """
)


def _rename(pairs: list[tuple[str, str]]) -> None:
    """把兩個 JSONB 欄位裡的 theme 值逐一換名（依 pairs 給定的方向）。"""
    bind = op.get_bind()
    for column in ("ai_output", "corrections"):
        stmt = sa.text(str(_SQL).format(column=column))
        for old, new in pairs:
            bind.execute(stmt, {"old": old, "new": new})


def upgrade() -> None:
    """舊值 → 新值。"""
    _rename(_THEME_RENAMES)


def downgrade() -> None:
    """新值 → 舊值（映射一對一，可完整回退）。"""
    _rename([(new, old) for old, new in _THEME_RENAMES])
