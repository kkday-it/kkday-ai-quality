"""歸因歷史（attribution_history）：評論級 append-only 事件流（初判快照 / 判決轉移 / 備註）。

一則評論 (source, source_id) 的時間軸由這些事件構成：
- kind='prejudge'：一次初判的完整歸因快照（replace_source_findings 同交易寫入）。
  **每次初判都留一列，不做去重**（2026-08-06 改）：使用者要能從時間軸看到「這則評論被跑過幾次」，
  而不只是「結果變過幾次」——舊的去重讓「跑了但結果一樣」與「根本沒跑到」在畫面上無法區分，
  且該次 job 的 LLM 日誌因為沒有歷史列掛載而從 UI 走不到。結果是否有變由 `result_digest`
  標記在 `params.unchanged`，交給前端顯示為「重跑·無變化」。
- kind='note'：評論級備註（綁 (source, source_id)，跨重新初判穩定）。

prejudge_runs 是 run 級、llm_usage 是 call 級；本表補「單一評論初判演進」缺口，
並以 model 維度為日後多模型對比鋪路。
"""

from __future__ import annotations

import hashlib
import json
import logging

from sqlalchemy import Connection, and_, select
from sqlalchemy import insert as sa_insert

from app.core.auth import SYSTEM_USER
from app.core.db import tables as T
from app.core.db._shared import live_attr_cond, select_wire, wire_row

_log = logging.getLogger(__name__)

# 事件出 API 的欄白名單 {wire 鍵: DB 欄名}（當前為恆等映射）。
# 與 `tests/test_wire_contract.py` 的 `_ATTRIBUTION_HISTORY_WIRE` 成對，改一邊必改另一邊。
_WIRE_COLS = {
    "id": "id",
    "source": "source",
    "source_id": "source_id",
    "kind": "kind",
    "model": "model",
    "params": "params",
    "attributions": "attributions",
    # result_digest 刻意不出 wire：它是「快照全欄位正規化 sha256」的**內部去重鍵**，
    # 前端從未消費（型別宣告過但零 template 使用），外露只是把實作細節推給消費端。
    "job_id": "job_id",
    "triggered_by": "triggered_by",
    "author": "author",
    "content": "content",
    "created_at": "created_at",
}


def snapshot_of(values: dict) -> dict:
    """attributions 落庫欄位 dict（_finding_values 產出）→ 歷史快照單筆（與回填 migration 同形）。

    只取初判本體欄。summary 存原始 JSONB 語系 map
    （zh-tw 顯示由前端取用）。
    """
    return {
        "polarity": values.get("polarity"),
        "sentiment_score": values.get("sentiment_score"),
        "stage": values.get("prejudge_stage"),
        "l1": {"code": values.get("l1_code"), "label": values.get("l1_label")},
        "l2": {"code": values.get("l2_code"), "label": values.get("l2_label")},
        "confidence": {
            "value": values.get("conf_value"),
            "raw": values.get("conf_raw"),
            "tier": values.get("conf_tier"),
        },
        "content": {
            "summary": values.get("summary"),
            "evidence": values.get("evidence"),
            "action": values.get("action"),
        },
        "is_primary": values.get("is_primary"),
    }


def result_digest(attributions: list[dict]) -> str:
    """快照陣列 → 正規化 sha256（去重比對鍵）。

    全欄位嚴格比對（使用者拍板）：快照含摘要措辭/信心值，任一欄漂移即視為結果變化；
    時間戳先天不入快照。排序鍵 (l1.code, l2.code, attribution_oid) 消除
    多歸因列序差異；default=str 兜底非 JSON 原生型別（Decimal 等）。
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


def _params_key(params: dict | None) -> dict:
    """參數比對鍵：剔除 `unchanged` 顯示旗標，只留真正的初判參數（model / prompt_versions…）。"""
    return {k: v for k, v in (params or {}).items() if k != "unchanged"}


def insert_prejudge_event(
    c: Connection,
    source: str,
    source_id: str,
    *,
    model: str,
    params: dict | None,
    attributions: list[dict],
    job_id: str | None,
    triggered_by: str | None,
) -> bool:
    """寫入一筆 kind='prejudge' 歷史——**每次初判都寫，不去重**。

    與最新一筆 model+params+digest 全同時，仍照寫，只在 `params.unchanged` 標記結果無變化
    （前端據此顯示「重跑·無變化」）。收呼叫端交易內的 connection（replace_source_findings 的
    `begin()` 區塊）——比對與插入必須和初判寫入同交易，且以 FOR UPDATE 鎖最新歷史列序列化
    並發重新初判，避免兩個並發 job 對「是否有變化」讀到彼此的中間態。

    Returns:
        本次結果是否與前一筆不同（True＝有變化；首筆亦為 True）。
    """
    h = T.attribution_history
    digest = result_digest(attributions)
    latest = c.execute(
        select(h.c.model, h.c.params, h.c.result_digest)
        .where(and_(h.c.source == source, h.c.source_id == source_id, h.c.kind == "prejudge"))
        .order_by(h.c.created_at.desc(), h.c.id.desc())
        .limit(1)
        .with_for_update()
    ).first()
    unchanged = (
        latest is not None
        and (latest.model or "") == (model or "")
        # 比對排除 `unchanged` 旗標本身：它是我們自己蓋上去的顯示標記，不是初判參數。
        # 不排除的話，「上一列有旗標、這一列還沒算出來」會讓 params 恆不相等，旗標永遠只蓋得上一次。
        and _params_key(latest.params) == _params_key(params)
        and latest.result_digest == digest
    )
    c.execute(
        sa_insert(h).values(
            source=source,
            source_id=source_id,
            kind="prejudge",
            model=model,
            params={**(params or {}), "unchanged": True} if unchanged else (params or {}),
            attributions=attributions,
            result_digest=digest,
            job_id=job_id or "",
            triggered_by=triggered_by or SYSTEM_USER,
        )
    )
    return not unchanged


def insert_manual_event(
    c,
    source: str,
    source_id: str,
    *,
    kind: str,
    params: dict,
    attributions: list[dict],
    author: str,
    reason: str = "",
) -> None:
    """寫入一筆**人工動作**事件（correction / review_confirm / suggestion_resolved）。

    收呼叫端交易內的 connection——人工動作與其事件必須同交易，否則會出現「改了但時間軸沒記」
    或反過來的半套狀態。

    `attributions` 一律傳**動作後該反饋的完整現值快照**（與 kind='prejudge' 同形）：前端
    `AttributionHistoryDrawer` 的「與前一次的差異」是 client-side 逐筆比對算出來的，同形才能讓
    那套邏輯原封不動跨事件型別工作，不必為每種新事件另寫一套 diff。

    理由寫進 `note_content` 這個 typed 欄而非埋在 params JSONB：理由要能被搜尋、導出、統計，
    符合本表「查詢密集用 typed 欄」的一貫立場。

    Args:
        kind: 事件型別（須在 `_USER_VISIBLE_KINDS` 內，否則寫得進去但時間軸看不到）。
        params: 事件細節（correction 存 {op, attribution_oid, changed}；review_confirm 存
            {attribution_oid, confirmed_fields}；suggestion_resolved 存 {batch_id, decisions}）。
        author: 動作執行者（無 SSO 時為 system，見 auth.actor）。
        reason: 人工填寫的理由（correction 必填；review_confirm 可空）。
    """
    c.execute(
        sa_insert(T.attribution_history).values(
            source=source,
            source_id=source_id,
            kind=kind,
            params=params,
            attributions=attributions,
            author=author,
            content=reason or None,
        )
    )


def insert_failure_event(
    source: str,
    source_id: str,
    *,
    error: str,
    job_id: str | None = None,
    triggered_by: str | None = None,
) -> None:
    """寫入一筆 kind='failure' 事件（初判失敗留痕；獨立交易、best-effort、絕不阻斷批次）。

    失敗筆不落 attributions（to_findings 拋錯前 replace_source_findings 未被呼叫），本表補其唯一持久痕跡：
    ① 供前端查「為何失敗」；② 供 prejudge_targets 依「最新成功後連續失敗數」設隱式重撈上限，防系統性
    失敗（壞 prompt / 失效 key）在 scope=all+unjudged 批次無限重撈。kind 是 Text 欄、新增邏輯值免 migration。
    寫入失敗僅 debug log 不拋（比照 llm_usage 落庫「輔助不阻斷初判」慣例）。
    """
    try:
        with T.get_engine().begin() as c:
            c.execute(
                sa_insert(T.attribution_history).values(
                    source=source,
                    source_id=source_id,
                    kind="failure",
                    params={"error": error},
                    job_id=job_id or "",
                    triggered_by=triggered_by or SYSTEM_USER,
                )
            )
    except Exception:  # noqa: BLE001  失敗留痕是輔助，寫不進去也不能拖垮初判批次
        _log.debug(
            "insert_failure_event 落庫失敗 source=%s id=%s", source, source_id, exc_info=True
        )


def _history_row(r) -> dict:
    """attribution_history 列 → API dict（顯式白名單 + created_at 轉 ISO 字串）。

    白名單使 DB 新增欄不會自動流進 API（見 `_shared.wire_row`）。
    """
    return wire_row(r, _WIRE_COLS)


# 使用者時間軸可見的事件類型（**白名單**，非黑名單）：kind 是 Text 欄、新增內部事件型別免 migration，
# 用黑名單的話每加一種內部 kind 都會靜默漏進 UI，被前端 v-else 兜底渲染成 author/content 皆空的
# 灰色「備註」——`failure` 就這樣在時間軸上假冒了 390 筆備註。白名單讓「不在清單上就不顯示」成為預設。
# 目前被擋在外的：`router_shadow`（域路由召回量測留痕，純內部遙測，對看評論歷史的人沒有意義）。
_USER_VISIBLE_KINDS = (
    "prejudge",
    "failure",
    "correction",
    "review_confirm",
    "suggestion",
    "suggestion_resolved",
)


def _slot_states(source: str, source_id: str) -> dict[str, dict]:
    """該反饋每個 L2 面向當下的狀態（供備註標示「這個面向現在還在不在」）。

    備註綁的是**面向**不是那一列歸因，所以歸因被改成別的分類、或被標記誤判之後，備註依然存在。
    這是刻意的（搬走等於改寫歷史），但畫面上必須講清楚，否則使用者會以為備註「掉了」。

    Returns:
        `l2_code` → `{"l2_label": str, "l1_label": str, "state": "live"|"dismissed"}`。
        查不到的面向＝該面向當下已無任何歸因，由 `_annotate_slot` 標成 `"gone"`。
    """
    jg = T.attributions
    stmt = select(jg.c.l1_code, jg.c.l1_label, jg.c.l2_code, jg.c.l2_label, jg.c.is_deleted).where(
        and_(jg.c.source == source, jg.c.source_id == source_id)
    )
    out: dict[str, dict] = {}
    with T.get_engine().connect() as c:
        for r in c.execute(stmt).mappings():
            if not r["l2_code"]:
                continue
            # 同一面向不可能有兩列（自然鍵唯一），故直接覆寫即可。
            out[r["l2_code"]] = {
                "l1_label": r["l1_label"] or r["l1_code"],
                "l2_label": r["l2_label"] or r["l2_code"],
                "state": "dismissed" if r["is_deleted"] else "live",
            }
    return out


def _annotate_slot(slot: dict | None, states: dict[str, dict]) -> dict | None:
    """把備註的槽位鍵補上顯示名與當下狀態；整則備註（slot=None）原樣回 None。

    補 label 是因為槽位鍵存的是 code（`quality` / `C-2-2`），直接顯示等於要使用者背代碼表；
    補 state 是因為「此面向目前已無歸因」這件事只有比對現值才知道，前端另撈一次是多一個往返。
    """
    if not slot or not slot.get("l2_code"):
        return slot
    hit = states.get(slot["l2_code"])
    return {
        **slot,
        "l1_label": hit["l1_label"] if hit else slot.get("l1_code"),
        "l2_label": hit["l2_label"] if hit else slot.get("l2_code"),
        "state": hit["state"] if hit else "gone",
    }


def list_attribution_history(source: str, source_id: str) -> list[dict]:
    """列某則反饋的**單一時間軸**（舊到新）：事件表的人可見事件 + 備註表的備註，按時間合併。

    只回 `_USER_VISIBLE_KINDS`——內部遙測事件不進使用者時間軸（原因見該常數註解）。

    **備註存在另一張表**（`attribution_note_lst`）卻在這裡合併，是刻意的分工：
    儲存層分開（備註的面向鍵是活查詢鍵，不能埋進事件表的凍結 params，理由見 db.notes 模組
    docstring），但**呈現層只有一條軸**——前端仍只吃一個來源，不必在 client 端 merge 兩份再排序，
    也不會讓「AI 判了 → 人改了 → 人留言說明」這條因果鏈被切成兩半。
    """
    from app.core.db import notes as _notes

    h = T.attribution_history
    stmt = (
        select_wire(h)
        .where(
            and_(
                h.c.source == source,
                h.c.source_id == source_id,
                h.c.kind.in_(_USER_VISIBLE_KINDS),
            )
        )
        .order_by(h.c.created_at.asc(), h.c.id.asc())
    )
    with T.get_engine().connect() as c:
        events = [_history_row(r) for r in c.execute(stmt).mappings()]

    slot_states = _slot_states(source, source_id)

    # 備註轉成與事件同形（kind='note'），前端一套渲染吃兩種來源。
    # `id` **保持 int**（不做 "note-N" 前綴）：wire 上同一個欄位有時是 int 有時是字串，正是
    # test_wire_contract 該擋的多型。兩張表的 id 會撞，但 `kind` 已足以區辨——前端用
    # `kind + id` 當複合 key（見 AttributionHistoryDrawer 的 v-for）。
    for n in _notes.list_notes(source, source_id):
        events.append(
            {
                "id": n["attribution_note_oid"],
                "source": n["source"],
                "source_id": n["source_id"],
                "kind": "note",
                "model": None,
                "params": {
                    "slot": _annotate_slot(n["slot"], slot_states),
                    "note_type": n["note_type"],
                },
                "attributions": None,
                "author": n["author"],
                "content": n["content"],
                "created_at": n["created_at"],
                # 事件專屬欄補 None：兩種來源在 wire 上**必須同形**，否則前端要為備註另寫一套
                # 渲染（而使用者看到的本來就該是一條軸）。少一個鍵 test_wire_contract 會紅。
                "job_id": None,
                "triggered_by": None,
            }
        )
    events.sort(key=lambda e: (e["created_at"] is None, e["created_at"], e["kind"], e["id"]))
    return events


def latest_snapshots(source: str, model: str) -> dict[str, dict]:
    """某來源下、指定模型的**每評論最新**初判快照（多模型對比導出用）。

    PG `DISTINCT ON (source_id)` + `ORDER BY source_id, created_at DESC, id DESC`＝每評論
    只取該模型最新一筆 kind='prejudge'（同模型重新初判多次只回最新；去重機制下相鄰快照必有差異）。
    SQLAlchemy `.distinct(col)` 為 PG 方言 DISTINCT ON——codebase 首用，語意由專測鎖定。

    Returns:
        {source_id: {"attributions": 快照陣列, "created_at": ISO 字串}}；該模型未初判過的評論不在其中。
    """
    h = T.attribution_history
    stmt = (
        select(h.c.source_id, h.c.attributions, h.c.created_at, h.c.params)
        .distinct(h.c.source_id)
        .where(and_(h.c.source == source, h.c.kind == "prejudge", h.c.model == model))
        .order_by(h.c.source_id, h.c.created_at.desc(), h.c.id.desc())
    )
    with T.get_engine().connect() as c:
        return {
            r.source_id: {
                "attributions": r.attributions or [],
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "params": r.params or {},
            }
            for r in c.execute(stmt)
        }


def list_prejudge_models() -> list[str]:
    """歷來實際初判過的模型清單（attributions 當前初判 ∪ attribution_history 快照，distinct 非空）。

    供前端「初判模型」篩選與導出「輸出結果版本」下拉選項。字母序；`stub`（無 key 假判）
    降權排最後——保留而非隱藏，否則純 stub 環境下拉空白、功能整支失效。
    """
    jg, h = T.attributions, T.attribution_history
    with T.get_engine().connect() as c:
        models = {
            r[0]
            for r in c.execute(
                # tombstone 過濾（本 query 不經 _jg_join_cond，需顯式補；見 _shared.live_attr_cond）
                select(jg.c.model)
                .distinct()
                .where(live_attr_cond(), jg.c.model.isnot(None), jg.c.model != "")
            )
        } | {
            r[0]
            for r in c.execute(
                select(h.c.model)
                .distinct()
                .where(h.c.kind == "prejudge", h.c.model.isnot(None), h.c.model != "")
            )
        }
    return sorted(models, key=lambda m: (m == "stub", m))
