"""重算 attribution_event_lst.result_digest——初判歷史去重已對全庫失效

**這是修 `b7d24e0a3f19` 的錯，而那支的錯源自 `2689374`。**

`insert_prejudge_event` 的去重是拿「本次新算的 digest」比對「最新一列存著的 digest」，
相同就 skip。digest 算的是快照的正規化 sha256——**快照少一個 key 就是完全不同的雜湊**。

事件鏈：
1. `2689374` 把 `finding_id` 從 `snapshot_of()` 的輸出移除（該欄退役）。此後新算的 digest
   不含該 key，而庫裡既有列的 digest 是**含**該 key 時算的 → 從那一刻起去重就對既有全庫失效
2. `b7d24e0a3f19` 把 `finding_id` 從既有快照的 JSONB 內容裡剝掉，卻**沒有重算 digest**。
   該支 docstring 還寫著「去重不受影響：`snapshot_of()` 從來就沒有輸出過 finding_id」
   ——**這句是錯的**，`2689374~1` 的版本第 9 行就是 `"finding_id": values.get("finding_id")`。
   基於錯誤前提的安全性論證自然也不成立

實測（本支套用前）：13,706 列 `kind='prejudge'`，`result_digest(當前快照)` 與存著的值
**全部對不上**。後果不是資料損失，是**同內容重判每次都會多寫一列歷史**——去重這個功能
對既有語料等於不存在。

修法：逐列以**當前**快照重算。演算法在本檔內**凍結一份副本**（而非 import
`attribution_history.result_digest`）：migration 一旦讀當下的 code，語意就會隨 code 漂移，
「這支 revision 做了什麼」不再可重現——同 baseline v2 的凍結式原則。

⚠️ **不能在 SQL 端複刻**：PG 的 jsonb 是按「key 長度 + 位元組序」排列，Python 的
`json.dumps(sort_keys=True)` 是字典序，兩者對多 key 物件會產出不同字串、不同雜湊
（本支第一版就是這樣寫的，實測 13,706 列全數對不上才發現）。

Revision ID: e9f1c04a7b23
Revises: d3b58e2c9017
Create Date: 2026-08-05

"""

import hashlib
import json
from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "e9f1c04a7b23"
down_revision: str | Sequence[str] | None = "d3b58e2c9017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHUNK = 2000


def _frozen_result_digest(attributions: list[dict]) -> str:
    """`attribution_history.result_digest` 於本 revision 當下的凍結副本。

    刻意複製而非 import：見 module docstring 的凍結式原則。若日後該函式的演算法要改，
    改的是它，本檔維持原樣——本 revision 的語意就該固定在這一刻。
    """
    ordered = sorted(
        attributions,
        key=lambda a: (
            (a.get("l1") or {}).get("code") or "",
            (a.get("l2") or {}).get("code") or "",
            str(a.get("attribution_oid") or ""),
        ),
    )
    payload = json.dumps(ordered, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def upgrade() -> None:
    """以當前快照重算全部 digest，讓去重重新生效。"""
    conn = op.get_bind()
    rows = conn.execute(
        text(
            "SELECT attribution_event_oid, attribution_snapshot "
            "FROM attribution_event_lst WHERE result_digest IS NOT NULL"
        )
    ).all()
    updates = [{"oid": oid, "d": _frozen_result_digest(snap or [])} for oid, snap in rows]
    for i in range(0, len(updates), _CHUNK):
        conn.execute(
            text(
                "UPDATE attribution_event_lst SET result_digest = :d "
                "WHERE attribution_event_oid = :oid"
            ),
            updates[i : i + _CHUNK],
        )


def downgrade() -> None:
    """不還原——舊 digest 是對著已經不存在的快照形狀算的，回填等於把失效狀態裝回去。"""
