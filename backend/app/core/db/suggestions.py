"""待審 LLM 建議：人工託管的反饋重新初判時，AI 結果不覆蓋現值而轉入本層待人工採納。

**為什麼需要這一層**：人工糾正過的歸因是「人看過原文後下的結論」，讓下一次批量初判把它靜默覆蓋
掉，等於告訴使用者「你改的東西隨時會不見」——實務上會讓人不敢改，那整套人工介入就白做了
（2026-08-04 退役的人工判決軸正是這樣死的）。所以人工值即現值，AI 的新結論降級為**建議**。

**本表語義是「當前尚未處理的建議」**，不是狀態機：

- 採納／駁回 ＝ 該列被 DELETE（決策本身記在 `attribution_event_lst` 的 suggestion_resolved 事件）
- 同一則反饋再次重新初判 ＝ 先清光舊 pending 再插新的（舊提案完整保存在前一筆 suggestion 事件的
  快照裡，不會遺失）

**已知取捨（刻意接受）**：駁回不記憶——下次重新初判用同 prompt 同模型會原封不動再提一次。真的
造成「駁回疲勞」時的解法是加一張抑制清單（比對 AI 值的 digest），但那是為了還沒發生的問題預先
抽象，先不做。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy import delete as sa_delete
from sqlalchemy import insert as sa_insert
from sqlalchemy import update as sa_update

from app.core.db import attribution_history as _history
from app.core.db import tables as T
from app.core.db._shared import attribution_dto, human_touched_cond

# 判定「AI 新結果與現值不同」的比對欄＝**判定內容本身**（面向由配對鍵承擔，不重複比）。
#
# ⚠️ 三個刻意的排除，每個都會造成「徽記天天亮、亮到沒人看」：
# - `conf_value` / `conf_raw`：浮點漂移；且人工列的信心值本來就是 NULL（原 AI 信心描述的是舊分類），
#   納入比對等於**每次重判必觸發**
# - `conf_tier`：人工列恆為 'human'、AI 列永遠不是——同上，必觸發（實測踩到）
# - `summary` / `evidence`：LLM 措辭天然會飄，同一個判斷換句話說不是「結論變了」
#
# 信心度**仍會顯示在對比 UI 上**（那是人決定採不採納的重要依據），只是它自己不觸發建議。
_DIFF_FIELDS = ("polarity", "sentiment_score", "prejudge_stage")

# 建議值欄位（attribution_suggestion_lst 與 attribution_tbl 同名的部分）。
_VALUE_COLS = (
    "polarity",
    "sentiment_score",
    "l1_code",
    "l1_label",
    "l2_code",
    "l2_label",
    "conf_value",
    "conf_raw",
    "conf_tier",
    "summary",
    "evidence",
)


def is_human_managed(c, source: str, source_id: str) -> bool:
    """該反饋是否已進入人工託管（任一列被人工新增／改值／標記誤判）。

    收呼叫端交易內的 connection——判定與後續寫入必須同交易，否則兩個並發重新初判可能同時讀到
    「還是 AI 託管」而雙雙走整組替換。
    """
    jg = T.attributions
    return bool(
        c.execute(
            select(jg.c.attribution_oid)
            .where(jg.c.source == source, jg.c.source_id == source_id, human_touched_cond())
            .limit(1)
        ).first()
    )


def _slot(row: Any) -> tuple[str, str]:
    """歸因的面向座標 (L1, L2)——建議與現值的配對鍵。"""
    return (row["l1_code"] or "", row["l2_code"] or "")


def diff_findings(current: list[dict], proposed: list[dict]) -> list[dict]:
    """現值 × AI 新結果 → 建議項清單（以面向為配對鍵）。

    Args:
        current: 現值列（DB 欄名 mapping，含 tombstone——tombstone 也要參與比對，否則
            「AI 又判出你已標記為誤判的面向」這件事會靜默消失）。
        proposed: 本次初判產出（`_finding_values` 形狀，DB 欄名）。

    Returns:
        [{change_type, attribution_oid, **建議值欄}]；同面向且比對欄全等 → **不產生建議項**
        （避免噪音）。
    """
    cur_by_slot = {_slot(r): r for r in current}
    new_by_slot = {_slot(r): r for r in proposed}
    items: list[dict] = []

    for slot, new in new_by_slot.items():
        old = cur_by_slot.get(slot)
        if old is None:
            items.append({"change_type": "add", "attribution_oid": None, **_values_of(new)})
            continue
        if old["is_deleted"]:
            # AI 又提出人工已標記為誤判的面向——這是最該讓人看到的一種建議
            items.append(
                {"change_type": "add", "attribution_oid": old["attribution_oid"], **_values_of(new)}
            )
            continue
        if any(old[f] != new.get(f) for f in _DIFF_FIELDS):
            items.append(
                {
                    "change_type": "replace",
                    "attribution_oid": old["attribution_oid"],
                    **_values_of(new),
                }
            )

    for slot, old in cur_by_slot.items():
        if slot not in new_by_slot and not old["is_deleted"]:
            items.append(
                {
                    "change_type": "remove",
                    "attribution_oid": old["attribution_oid"],
                    **_values_of(old),
                }
            )
    return items


def _values_of(row: Any) -> dict:
    """取建議值欄（現值列與新結果列的欄名一致，故共用）。"""
    return {k: row.get(k) if isinstance(row, dict) else row[k] for k in _VALUE_COLS}


def write_suggestions(
    c,
    source: str,
    source_id: str,
    items: list[dict],
    *,
    model: str,
    job_id: str | None,
    author: str,
) -> str:
    """清掉舊 pending 建議 → 寫入本輪建議（同交易）；回 batch_id。"""
    sg = T.attribution_suggestions
    batch_id = f"{job_id or uuid.uuid4().hex[:12]}:{source_id}"
    c.execute(sa_delete(sg).where(sg.c.feedback_source_code == source, sg.c.source_id == source_id))
    for it in items:
        c.execute(
            sa_insert(sg).values(
                feedback_source_code=source,
                source_id=source_id,
                suggestion_batch_id=batch_id,
                model=model,
                job_id=job_id,
                create_user=author or None,
                **it,
            )
        )
    return batch_id


def pending_counts(source: str, source_ids: list[str]) -> dict[str, int]:
    """一批反饋各有幾條待審建議（列表徽記用；一次查完避免 N+1）。"""
    if not source_ids:
        return {}
    sg = T.attribution_suggestions
    with T.get_engine().connect() as c:
        rows = c.execute(
            select(sg.c.source_id, func.count().label("n"))
            .where(sg.c.feedback_source_code == source, sg.c.source_id.in_(source_ids))
            .group_by(sg.c.source_id)
        ).all()
    return {r[0]: int(r[1]) for r in rows}


def list_pending_suggestions(source: str, source_id: str) -> dict:
    """某則反饋的待審建議 → {batch_id, model, created_at, items}。

    每個 item 同時帶 `current`（人工現值）與 `proposed`（LLM 新值），**兩側同形**（皆為
    `attribution_dto` 形狀）——這樣前端對比 UI 用同一個渲染函式跑兩欄即可，省掉一半程式碼。
    """
    sg, jg = T.attribution_suggestions, T.attributions
    with T.get_engine().connect() as c:
        rows = (
            c.execute(
                select(sg)
                .where(sg.c.feedback_source_code == source, sg.c.source_id == source_id)
                .order_by(sg.c.attribution_suggestion_oid)
            )
            .mappings()
            .all()
        )
        if not rows:
            return {"batch_id": None, "model": None, "created_at": None, "items": []}
        oids = [r["attribution_oid"] for r in rows if r["attribution_oid"]]
        cur = {}
        if oids:
            cur = {
                r["attribution_oid"]: dict(r)
                for r in c.execute(select(jg).where(jg.c.attribution_oid.in_(oids)))
                .mappings()
                .all()
            }
    items = [
        {
            "suggestion_oid": r["attribution_suggestion_oid"],
            "change_type": r["change_type"],
            "attribution_oid": r["attribution_oid"],
            "current": attribution_dto(cur[r["attribution_oid"]])
            if r["attribution_oid"] in cur
            else None,
            "proposed": attribution_dto({**{k: r[k] for k in _VALUE_COLS}, "model": r["model"]}),
        }
        for r in rows
    ]
    first = rows[0]
    return {
        "batch_id": first["suggestion_batch_id"],
        "model": first["model"],
        "created_at": first["create_date"].isoformat() if first["create_date"] else None,
        "items": items,
    }


def resolve_suggestions(
    source: str,
    source_id: str,
    batch_id: str,
    decisions: list[dict],
    *,
    reason: str,
    author: str,
) -> dict:
    """採納／駁回建議（單一交易）→ {applied, rejected, remaining}。

    採納語義：
    - `replace` → 現值列更新為建議值，**`is_human_corrected` 保持 true**（單向閂鎖：一旦人工
      託管就永遠人工託管，否則下一次重新初判又會靜默覆蓋）
    - `add` → 新增一列（或還原被標記誤判的那一列並套用新值）
    - `remove` → 現值列設 tombstone（不硬刪，保持自然鍵佔用一致）

    駁回一律不動 `attribution_tbl`。無論採納或駁回，被處理的建議列都直接刪除。

    Raises:
        ValueError: batch_id 與現存 pending 批不符（兩人同時處理 → 要求重新載入）。
    """
    sg, jg = T.attribution_suggestions, T.attributions
    by_oid = {int(d["suggestion_oid"]): d.get("decision") for d in decisions}
    applied = rejected = 0
    with T.get_engine().begin() as c:
        rows = (
            c.execute(
                select(sg).where(
                    sg.c.feedback_source_code == source,
                    sg.c.source_id == source_id,
                    sg.c.attribution_suggestion_oid.in_(list(by_oid) or [-1]),
                )
            )
            .mappings()
            .all()
        )
        stale = [r for r in rows if r["suggestion_batch_id"] != batch_id]
        if stale:
            raise ValueError("建議已被更新（有更新的一批重新初判結果），請重新載入後再處理")

        for r in rows:
            decision = by_oid.get(r["attribution_suggestion_oid"])
            if decision != "accept":
                rejected += 1
                continue
            applied += 1
            values = {k: r[k] for k in _VALUE_COLS}
            if r["change_type"] == "remove":
                c.execute(
                    sa_update(jg)
                    .where(jg.c.attribution_oid == r["attribution_oid"])
                    .values(is_deleted=True, modify_user=author, modify_date=func.now())
                )
            elif r["attribution_oid"]:
                c.execute(
                    sa_update(jg)
                    .where(jg.c.attribution_oid == r["attribution_oid"])
                    .values(
                        **values,
                        is_deleted=False,
                        model=r["model"],
                        modify_user=author,
                        modify_date=func.now(),
                    )
                )
            else:
                c.execute(
                    sa_insert(jg).values(
                        source=source,
                        source_id=source_id,
                        **values,
                        prejudge_stage="judged",
                        model=r["model"],
                        is_primary=False,
                        is_auto_accepted=False,
                        created_at=func.now(),
                        create_user=author,
                    )
                )
        c.execute(
            sa_delete(sg).where(
                sg.c.attribution_suggestion_oid.in_([r["attribution_suggestion_oid"] for r in rows])
                if rows
                else sg.c.attribution_suggestion_oid == -1
            )
        )
        remaining = (
            c.execute(
                select(func.count()).where(
                    and_(sg.c.feedback_source_code == source, sg.c.source_id == source_id)
                )
            ).scalar()
            or 0
        )
        _history.insert_manual_event(
            c,
            source,
            source_id,
            kind="suggestion_resolved",
            params={
                "batch_id": batch_id,
                "applied": applied,
                "rejected": rejected,
                "decisions": [
                    {
                        "suggestion_oid": r["attribution_suggestion_oid"],
                        "change_type": r["change_type"],
                        "decision": by_oid.get(r["attribution_suggestion_oid"]),
                    }
                    for r in rows
                ],
            },
            attributions=_snapshot_live(c, source, source_id),
            author=author,
            reason=reason,
        )
    return {"applied": applied, "rejected": rejected, "remaining": int(remaining)}


def _snapshot_live(c, source: str, source_id: str) -> list[dict]:
    """處理後的完整現值快照（與 kind='prejudge' 同形，前端 diff 邏輯共用）。"""
    from app.core.db.corrections import _snapshot_current

    return _snapshot_current(c, source, source_id)
