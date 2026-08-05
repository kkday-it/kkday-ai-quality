"""清掉歷史快照 JSONB 內殘留的 finding_id

`e2a91c47d0b3` 刪掉了 `attribution_tbl.finding_id` 欄，但同一個值還以 JSON key 的形式
埋在 `attribution_event_lst.attribution_snapshot` 的每則歸因裡（實測 11,253 / 11,253 則
全部帶有），屬同一次退役沒清乾淨的殘留。

清掉是安全的，兩個理由：
⚠️ **原本這裡還寫了第三個理由「去重不受影響」，那是錯的**（2026-08-05 稽核抓到）：
   `snapshot_of()` **確實輸出過** finding_id（見 `2689374~1` 的第 9 行），是同一批 commit 才
   拿掉的。既有列的 digest 是含該 key 時算的，剝掉內容卻不重算 digest，等於讓去重對既有
   全庫永久失效（實測 13,706 列全數對不上）。已由 `e9f1c04a7b23` 重算修復。
① **前端沒有渲染它**：`AttributionHistoryDrawer` 的 `Snap` 型別列了這個欄，但 template
   從未使用（本支同輪一併移除該型別欄）。
② 值本身 100% 可由 (source, source_id, l1_code, l2_code) 推導，且其中 91% 帶的是 2026-07-01
   改名前的舊 source code，與同列的 `feedback_source_code` 互相矛盾——留著只會誤導。

⚠️ 不可逆（套用前已 pg_dump 至 ~/kkday-backups/）。downgrade 不回填：值無法從剩餘欄位
還原出「原本錯誤的那個字串」，硬湊只會製造第二種假資料。

Revision ID: b7d24e0a3f19
Revises: a3f6520ce8d1
Create Date: 2026-08-04

"""

from collections.abc import Sequence

from alembic import op

revision: str = "b7d24e0a3f19"
down_revision: str | Sequence[str] | None = "a3f6520ce8d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# jsonb 陣列逐元素 `- 'finding_id'` 後重組。只掃真的含該 key 的列（`@?` 走 jsonb path，
# 有 GIN 索引時可用；此表沒有也只是全表掃 14k 列，秒級）。
_STRIP = """
UPDATE attribution_event_lst
   SET attribution_snapshot = (
       SELECT jsonb_agg(s - 'finding_id' ORDER BY ord)
         FROM jsonb_array_elements(attribution_snapshot) WITH ORDINALITY AS t(s, ord)
   )
 WHERE attribution_snapshot IS NOT NULL
   AND jsonb_typeof(attribution_snapshot) = 'array'
   AND attribution_snapshot @? '$[*].finding_id'
"""


def upgrade() -> None:
    """從歷史快照的每則歸因移除 finding_id key。"""
    op.execute(_STRIP)


def downgrade() -> None:
    """不回填——原值無法從剩餘欄位重建（見 module docstring）。"""
