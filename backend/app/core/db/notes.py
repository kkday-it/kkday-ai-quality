"""反饋備註（append-only）：整則備註與面向備註共用一張表，靠 l1_code/l2_code 是否有值區分。

**為什麼綁面向而不綁 attribution_oid**——這是本模組唯一一條非做對不可的事：

歸因級備註曾經存在也曾經死過。`finding_notes` 表 2026-08-04 隨 migration `b2f47c9e15a3` 退役，
死因寫在該檔第 11 行：「8 列中 6 列已是孤兒（finding_id 無對應 attributions）」。根因是它綁了
流水號，而 `replace_source_findings` 對 AI 託管反饋是「DELETE 全部 + INSERT 新列」——備註指向的
號碼就此指向虛空。

改綁 `(source, source_id, l1_code, l2_code)` 這組**跨重判穩定的面向鍵**之後：

- 面向還在 → 備註正常掛上
- 面向消失（人改了分類 / AI 重判沒再判出）→ 備註仍**自我描述**：「這則反饋在『餐飲品質』上，
  某人曾說過 X」。語義完整、讀得懂，不是 `finding_id=1234` 那種無法解讀的資料垃圾

**為什麼不塞進 attribution_event_lst 的 params**：那張表的 `params` 承載的是「事件當下發生了
什麼」的凍結快照（`correction` 的 changed、`suggestion` 的 batch_id，寫完就不再被查詢的死指標），
而備註的面向鍵是**活的查詢鍵**——每次開工作台都要按面向撈、列表要算數量。活查詢鍵埋進凍結
JSONB，就永遠無法建索引、無法對 note_type 做參照約束、每次查詢都要走 JSONB path。兩者生命週期
根本不同，不該共用一個容器。

**append-only**：表上刻意沒有 modify_user / modify_date。備註是互動軌跡，「已聯繫供應商」寫出去
之後若能改成別的，這條軌跡的稽核價值就沒了。
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy import insert as sa_insert

from app.core.db import tables as T
from app.core.db._shared import _iso_if_dt


class NoteError(Exception):
    """備註寫入的可預期錯誤（code 由 router 映射成 4xx）。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _dto(r) -> dict:
    """單列 → API DTO（面向兩欄收成巢狀 slot，`None`＝整則備註）。"""
    slot = (
        {"l1_code": r["l1_code"], "l2_code": r["l2_code"]}
        if r["l1_code"] and r["l2_code"]
        else None
    )
    return {
        "attribution_note_oid": r["attribution_note_oid"],
        "source": r["source"],
        "source_id": r["source_id"],
        "slot": slot,
        "note_type": r["note_type"],
        "content": r["content"],
        "author": r["create_user"],
        # 時間一律轉 ISO 字串：與 attribution_history 的 wire_row 同型。兩者要在同一條
        # 時間軸上排序，型別不一致（datetime vs str）會直接拋 TypeError。
        "created_at": _iso_if_dt(r["create_date"]),
    }


def active_note_types() -> list[dict]:
    """可選的互動類型（`attribution_dimension_master` 的 note_type 軸，僅啟用項，依 sort_order）。"""
    d = T.attribution_dimensions
    with T.get_engine().connect() as c:
        rows = (
            c.execute(
                select(d.c.item_code, d.c.item_label, d.c.item_desc)
                .where(d.c.dimension_code == "note_type", d.c.is_active.is_(True))
                .order_by(d.c.sort_order, d.c.item_code)
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


def add_note(
    source: str,
    source_id: str,
    *,
    note_type: str,
    content: str,
    author: str,
    l1_code: str | None = None,
    l2_code: str | None = None,
) -> dict:
    """新增一則備註（append-only）→ 回落庫後的 DTO。

    Args:
        source: 反饋來源 code。
        source_id: 該來源的特徵 id。
        note_type: 互動類型機器碼；須在 `attribution_dimension_master` 的 note_type 軸且啟用。
        content: 備註內容（非空）。
        author: 留言者（無 SSO 時為 system，見 auth.actor）。
        l1_code: 面向的歸因域碼；與 l2_code 同時給＝面向備註，同時省略＝整則備註。
        l2_code: 面向碼。

        **刻意不驗證「該面向當下有沒有歸因」**：允許對已消失的面向補記——那正是綁面向鍵
        而非 oid 的目的（「這個面向當初被判過、後來被我改掉了，原因是…」是有價值的紀錄）。

    Raises:
        NoteError: `invalid`（內容空／面向兩欄只給一個）、`unknown_type`（類型不在啟用值域）。
    """
    text = (content or "").strip()
    if not text:
        raise NoteError("invalid", "備註內容不可為空")
    if bool(l1_code) != bool(l2_code):
        raise NoteError(
            "invalid", "面向備註必須同時提供 l1_code 與 l2_code（兩者皆省略＝整則備註）"
        )
    if note_type not in {t["item_code"] for t in active_note_types()}:
        raise NoteError("unknown_type", f"互動類型不在啟用值域內：{note_type!r}")

    n = T.attribution_notes
    with T.get_engine().begin() as c:
        row = (
            c.execute(
                sa_insert(n)
                .values(
                    source=source,
                    source_id=source_id,
                    l1_code=l1_code,
                    l2_code=l2_code,
                    note_type=note_type,
                    content=text,
                    create_user=author,
                )
                .returning(*n.c)
            )
            .mappings()
            .first()
        )
    return _dto(row)


def list_notes(source: str, source_id: str) -> list[dict]:
    """某則反饋的全部備註（舊到新，與時間軸同向）。"""
    n = T.attribution_notes
    with T.get_engine().connect() as c:
        rows = (
            c.execute(
                select(n)
                .where(n.c.source == source, n.c.source_id == source_id)
                .order_by(n.c.create_date, n.c.attribution_note_oid)
            )
            .mappings()
            .all()
        )
    return [_dto(r) for r in rows]


def note_counts(source: str, source_ids: list[str]) -> dict[str, int]:
    """一批反饋各有幾則備註（列表徽記用；一次查完避免 N+1，同 suggestions.pending_counts 樣式）。"""
    if not source_ids:
        return {}
    n = T.attribution_notes
    with T.get_engine().connect() as c:
        rows = c.execute(
            select(n.c.source_id, func.count().label("n"))
            .where(n.c.source == source, n.c.source_id.in_(source_ids))
            .group_by(n.c.source_id)
        ).all()
    return {str(sid): int(cnt) for sid, cnt in rows}
