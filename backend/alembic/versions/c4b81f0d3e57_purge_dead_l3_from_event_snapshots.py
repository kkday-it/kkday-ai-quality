"""清除事件快照裡的死 l3 鍵（初判 l3_* 退役的最後殘留）

初判歸因的 L3 層在 2026-07-14 退役（拔 l3_*／model_votes／ensemble），但
``attribution_event_lst.attribution_snapshot`` 裡的歷史快照仍帶著這個鍵。

為何是「清除」而非「保留歷史」——實測全庫零筆有真實值：
    {"code": "", "label": ""}    6,246 個元素
    {"code": null, "label": null}  925 個元素
    （無此鍵）                    4,226 個元素
它不是歷史紀錄，是雜訊；而且會主動製造問題——前端 AttributionHistoryDrawer 對連續
快照做 client-side diff，`l3` 在「有鍵」與「無鍵」的快照之間跳動時會被渲染成一次假的
欄位變更。清掉零資訊損失。

安全性：只移除 `l3` 這一個鍵，快照的其餘結構逐位元組不動；jsonb 的 `-` 運算子對
不存在的鍵是 no-op，故本 migration 冪等、可重複執行。

⚠️ 不可逆（downgrade 為 no-op）：被移除的值全是空字串／null，重建它們沒有意義，
且無法區辨「原本就沒這個鍵」與「被本 migration 移除」。刻意不留還原路徑，
符合專案原則 4（退役即徹底，不留「歷史相容」死欄）。

Revision ID: c4b81f0d3e57
Revises: b7d419e0c852
"""

from __future__ import annotations

from alembic import op

revision = "c4b81f0d3e57"
down_revision = "b7d419e0c852"
branch_labels = None
depends_on = None

# 快照為 attribution 物件的 JSON 陣列；逐元素移除 l3 鍵後重新聚合回陣列。
# 只更新真的含 l3 的列（WHERE 過濾），避免無謂改寫與 autovacuum 壓力。
_PURGE = """
UPDATE attribution_event_lst AS e
SET attribution_snapshot = sub.cleaned
FROM (
    SELECT x.attribution_event_oid AS oid,
           COALESCE(jsonb_agg(x.elem - 'l3' ORDER BY x.ord), '[]'::jsonb) AS cleaned
    FROM (
        SELECT a.attribution_event_oid,
               elem,
               ord
        FROM attribution_event_lst a,
             LATERAL jsonb_array_elements(a.attribution_snapshot) WITH ORDINALITY AS t(elem, ord)
        WHERE jsonb_typeof(a.attribution_snapshot) = 'array'
          AND a.attribution_snapshot::text LIKE '%"l3"%'
    ) AS x
    GROUP BY x.attribution_event_oid
) AS sub
WHERE e.attribution_event_oid = sub.oid
"""


def upgrade() -> None:
    """逐元素移除 l3 鍵；其餘欄位與元素順序（WITH ORDINALITY）完全保留。"""
    op.execute(_PURGE)


def downgrade() -> None:
    """no-op：移除的值全為空字串／null，還原無意義且無法與「原本就沒有」區辨。"""
