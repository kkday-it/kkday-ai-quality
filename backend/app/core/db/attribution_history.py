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
from app.core.db._shared import select_wire, wire_row

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
_USER_VISIBLE_KINDS = ("prejudge", "note", "failure")


def list_attribution_history(source: str, source_id: str) -> list[dict]:
    """列某則評論的歸因歷史時間軸（舊到新，時間遞增；初判快照 / 備註 / 初判失敗三類事件混排）。

    只回 `_USER_VISIBLE_KINDS`——內部遙測事件不進使用者時間軸（原因見該常數註解）。
    """
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
        return [_history_row(r) for r in c.execute(stmt).mappings()]


def add_history_note(source: str, source_id: str, *, author: str, content: str) -> dict:
    """新增一則評論級備註（kind='note'，append-only）；回傳建立列（含 id / 時間）。"""
    ins = (
        sa_insert(T.attribution_history)
        .values(source=source, source_id=source_id, kind="note", author=author, content=content)
        .returning(*T.attribution_history.c)
    )
    with T.get_engine().begin() as c:
        return _history_row(c.execute(ins).mappings().first())


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
                select(jg.c.model).distinct().where(jg.c.model.isnot(None), jg.c.model != "")
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
