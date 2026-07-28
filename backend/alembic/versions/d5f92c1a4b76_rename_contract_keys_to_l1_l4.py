"""售後根因契約鍵改名：theme/category/likely_cause/modify_target → L1/L2/L3/L4

Revision ID: d5f92c1a4b76
Revises: c4e81b7a35d2
Create Date: 2026-07-28 19:30:00.000000

2026-07-28 起契約四鍵統一為層級代號（跑批 CSV 表頭、Structured Outputs schema、Prompt 快照同批
升版）。人工評判案例庫存的是**以舊鍵名為鍵的字典**，不跟著改鍵，回歸重跑會整批讀不到欄位：
`prompt_regression` 是拿 `corrections`/`confirmed` 的鍵去比對 AI 新輸出的鍵，鍵名對不上＝該欄
視同「人沒看過」，分數會憑空虛高（與 c4e81b7a35d2 改「值」的理由同源，這支改的是「鍵」）。

動 `prompt_debug_reviews` 三個 JSONB 欄位：
- `ai_output`（AI 當時判的全欄）、`corrections`（人填的正解 {欄名: 值}）→ 字典**鍵名**改名
- `confirmed`（人標對的欄名字串陣列）→ 陣列**元素值**改名（它存的就是欄名本身）
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5f92c1a4b76"
down_revision: str | Sequence[str] | None = "c4e81b7a35d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 舊鍵 → 新鍵。新舊鍵集合不相交（L1–L4 是全新名稱），故逐鍵改名無先後順序問題，
# downgrade 直接反轉即可。
_KEY_RENAMES: list[tuple[str, str]] = [
    ("theme", "L1"),
    ("category", "L2"),
    ("likely_cause", "L3"),
    ("modify_target", "L4"),
]

# 字典鍵改名：移除舊鍵後併回新鍵。`?` 存在性判斷不可省——corrections 只存被標錯的那幾欄，
# 沒有該鍵的列若照改，`->` 會回 NULL 而把一個 null 值的新鍵硬塞進去。
_RENAME_DICT_KEY = """
    UPDATE prompt_debug_reviews
       SET {column} = ({column} - CAST(:old AS text))
                      || jsonb_build_object(CAST(:new AS text), {column} -> CAST(:old AS text))
     WHERE {column} ? CAST(:old AS text)
"""

# 陣列元素改名：confirmed 存的是欄名字串，逐元素比對替換後重組。
# jsonb_agg 對空陣列會回 NULL，故 WHERE 用 @> 先篩掉「不含該欄名」的列（含空陣列）。
_RENAME_ARRAY_ELEM = """
    UPDATE prompt_debug_reviews
       SET confirmed = (
               SELECT jsonb_agg(
                          CASE WHEN elem = to_jsonb(CAST(:old AS text))
                               THEN to_jsonb(CAST(:new AS text))
                               ELSE elem END
                      )
                 FROM jsonb_array_elements(confirmed) AS elem
           )
     WHERE confirmed @> jsonb_build_array(CAST(:old AS text))
"""


def _rename(pairs: list[tuple[str, str]]) -> None:
    """把三個 JSONB 欄位裡的契約欄名逐一換名（依 pairs 給定的方向）。"""
    bind = op.get_bind()
    for column in ("ai_output", "corrections"):
        stmt = sa.text(_RENAME_DICT_KEY.format(column=column))
        for old, new in pairs:
            bind.execute(stmt, {"old": old, "new": new})
    array_stmt = sa.text(_RENAME_ARRAY_ELEM)
    for old, new in pairs:
        bind.execute(array_stmt, {"old": old, "new": new})


def upgrade() -> None:
    """舊鍵 → 新鍵。"""
    _rename(_KEY_RENAMES)


def downgrade() -> None:
    """新鍵 → 舊鍵（映射一對一，可完整回退）。"""
    _rename([(new, old) for old, new in _KEY_RENAMES])
