"""L2 跳出值升版：`__OUT_OF_TAXONOMY__` → 其他

Revision ID: f3a81c6e5d92
Revises: d5f92c1a4b76
Create Date: 2026-07-30 12:00:00.000000

2026-07-30 起 L2 的跳出值由哨兵 `__OUT_OF_TAXONOMY__` 改為「其他」，與 L1 同值。
改動動機：模型輸出值、schema enum、表格顯示三處口徑原本不一致——落表層早就把哨兵映射
成「其他」顯示，只有模型要逐字輸出的值還是哨兵。收斂為單一值後，`_csv_row` 的 L2 映射
同批退役（L3 的 `unclear → 其他` 是獨立過渡層，保留）。

案例庫存的是**當時判定與人工正解的逐字值**，不跟著遷就會在回歸重跑時把舊案例判成「還是
不對」——`prompt_regression.compare_case` 比對的是字串相等。同 `c4e81b7a35d2`（L1 那次
改名）的處理。

只動 `prompt_debug_reviews` 的兩個 JSONB 欄位：`ai_output.L2`（AI 當時判的）與
`corrections.L2`（人工填的正解）。`confirmed` 存的是欄位鍵名不是值，不受影響。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3a81c6e5d92"
down_revision: str | Sequence[str] | None = "d5f92c1a4b76"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 舊值 → 新值。一對一且雙向唯一，故 downgrade 直接反轉即可。
_L2_RENAMES: list[tuple[str, str]] = [
    ("__OUT_OF_TAXONOMY__", "其他"),
]

# 只在該欄現值等於舊值時才寫入：jsonb_set 預設 create_missing=true，沒有 WHERE 會給
# 原本沒有 L2 鍵的列硬塞一個出來（corrections 常常只存人工改過的那幾欄）。
_SQL = sa.text(
    """
    UPDATE prompt_debug_reviews
       SET {column} = jsonb_set({column}, '{{L2}}', to_jsonb(CAST(:new AS text)))
     WHERE {column}->>'L2' = :old
    """
)


def _rename(pairs: list[tuple[str, str]]) -> None:
    """把兩個 JSONB 欄位裡的 L2 值逐一換名（依 pairs 給定的方向）。"""
    bind = op.get_bind()
    for column in ("ai_output", "corrections"):
        stmt = sa.text(str(_SQL).format(column=column))
        for old, new in pairs:
            bind.execute(stmt, {"old": old, "new": new})


def upgrade() -> None:
    """舊值 → 新值。"""
    _rename(_L2_RENAMES)


def downgrade() -> None:
    """新值 → 舊值（映射一對一，可完整回退）。"""
    _rename([(new, old) for old, new in _L2_RENAMES])
