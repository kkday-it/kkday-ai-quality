"""判決歷史（judgment_history）：評論級 append-only 事件流（判決快照 / 覆核轉移 / 備註）。

一則評論 (source, source_id) 的時間軸由三類事件構成：
- kind='judgment'：一次判決的完整歸因快照（replace_source_findings 同交易寫入；
  model+params+result_digest 與最新一筆完全相同即 skip——全欄位嚴格去重）。
- kind='status'：人工覆核轉移（update/batch_update_finding_status 寫入；params 記
  {to, changes:[{finding_id, from}]}，恆記錄不去重）。
- kind='note'：評論級備註（與 finding 級 finding_notes 並存，兩個入口）。

judgment_runs 是 run 級、llm_usage 是 call 級；本表補「單一評論判決演進」缺口，
並以 model 維度為日後多模型對比鋪路。
"""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import Connection, and_, select
from sqlalchemy import insert as sa_insert

from app.core.db import tables as T


def snapshot_of(values: dict) -> dict:
    """judgments 落庫欄位 dict（_finding_values 產出）→ 歷史快照單筆（與回填 migration 同形）。

    只取判決本體欄；人工覆核軸（status）不入快照——重判會保留人工覆核結果，
    若入快照，「判決相同但先前已被人工確認」會被誤判為結果變化；覆核轉移由 kind='status'
    事件獨立留痕。summary 存原始 JSONB 語系 map（zh-tw 顯示由前端取用）。
    """
    return {
        "finding_id": values.get("finding_id"),
        "polarity": values.get("polarity"),
        "sentiment_score": values.get("sentiment_score"),
        "stage": values.get("stage"),
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
    僅 judged_at 時戳先天不入快照。排序鍵 (l1.code, l2.code, finding_id) 消除
    多歸因列序差異；default=str 兜底非 JSON 原生型別（Decimal 等）。
    """
    ordered = sorted(
        attributions,
        key=lambda a: (
            (a.get("l1") or {}).get("code") or "",
            (a.get("l2") or {}).get("code") or "",
            a.get("finding_id") or "",
        ),
    )
    payload = json.dumps(ordered, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def insert_judgment_event(
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
    """寫入一筆 kind='judgment' 歷史（去重：與最新一筆 model+params+digest 全同即 skip）。

    收呼叫端交易內的 connection（replace_source_findings 的 `begin()` 區塊）——比對與插入
    必須和判決寫入同交易，且以 FOR UPDATE 鎖最新歷史列序列化並發重判，否則兩個並發 job
    可能同時讀到「無變化」而漏記、或雙寫重複列（TOCTOU）。

    Returns:
        是否實際插入（False＝與前一筆完全相同已 skip）。
    """
    h = T.judgment_history
    digest = result_digest(attributions)
    latest = c.execute(
        select(h.c.model, h.c.params, h.c.result_digest)
        .where(and_(h.c.source == source, h.c.source_id == source_id, h.c.kind == "judgment"))
        .order_by(h.c.created_at.desc(), h.c.id.desc())
        .limit(1)
        .with_for_update()
    ).first()
    if (
        latest is not None
        and (latest.model or "") == (model or "")
        and (latest.params or {}) == (params or {})
        and latest.result_digest == digest
    ):
        return False
    c.execute(
        sa_insert(h).values(
            source=source,
            source_id=source_id,
            kind="judgment",
            model=model,
            params=params or {},
            attributions=attributions,
            result_digest=digest,
            job_id=job_id or "",
            triggered_by=triggered_by or "",
        )
    )
    return True


def insert_status_event(
    c: Connection,
    source: str,
    source_id: str,
    *,
    to_status: str,
    changes: list[dict],
    author: str | None,
) -> None:
    """寫入一筆 kind='status' 覆核轉移事件（恆記錄不去重；同交易由呼叫端保證）。

    changes：[{finding_id, from}]——單筆轉移一項、批量轉移多項；目標狀態統一存 params.to。
    """
    c.execute(
        sa_insert(T.judgment_history).values(
            source=source,
            source_id=source_id,
            kind="status",
            params={"to": to_status, "changes": changes},
            author=author or "",
        )
    )


def _history_row(r: dict) -> dict:
    """judgment_history 列 → API dict（created_at ISO 字串，比照 finding_notes/judgment_runs 慣例）。"""
    v = r.get("created_at")
    r["created_at"] = v.isoformat() if v is not None and hasattr(v, "isoformat") else v
    return r


def list_judgment_history(source: str, source_id: str) -> list[dict]:
    """列某則評論的判決歷史時間軸（新到舊；判決快照 / 覆核轉移 / 備註三類事件混排）。"""
    h = T.judgment_history
    stmt = (
        select(h)
        .where(and_(h.c.source == source, h.c.source_id == source_id))
        .order_by(h.c.created_at.desc(), h.c.id.desc())
    )
    with T.get_engine().connect() as c:
        return [_history_row(dict(r)) for r in c.execute(stmt).mappings()]


def add_history_note(source: str, source_id: str, *, author: str, content: str) -> dict:
    """新增一則評論級備註（kind='note'，append-only）；回傳建立列（含 id / 時間）。"""
    ins = (
        sa_insert(T.judgment_history)
        .values(source=source, source_id=source_id, kind="note", author=author, content=content)
        .returning(*T.judgment_history.c)
    )
    with T.get_engine().begin() as c:
        return _history_row(dict(c.execute(ins).mappings().first()))


def latest_snapshots(source: str, model: str) -> dict[str, dict]:
    """某來源下、指定模型的**每評論最新**判決快照（多模型對比導出用）。

    PG `DISTINCT ON (source_id)` + `ORDER BY source_id, created_at DESC, id DESC`＝每評論
    只取該模型最新一筆 kind='judgment'（同模型重判多次只回最新；去重機制下相鄰快照必有差異）。
    SQLAlchemy `.distinct(col)` 為 PG 方言 DISTINCT ON——codebase 首用，語意由專測鎖定。

    Returns:
        {source_id: {"attributions": 快照陣列, "created_at": ISO 字串}}；該模型未判過的評論不在其中。
    """
    h = T.judgment_history
    stmt = (
        select(h.c.source_id, h.c.attributions, h.c.created_at)
        .distinct(h.c.source_id)
        .where(and_(h.c.source == source, h.c.kind == "judgment", h.c.model == model))
        .order_by(h.c.source_id, h.c.created_at.desc(), h.c.id.desc())
    )
    with T.get_engine().connect() as c:
        return {
            r.source_id: {
                "attributions": r.attributions or [],
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in c.execute(stmt)
        }


def list_judgment_models() -> list[str]:
    """歷來實際判決過的模型清單（judgments 當前判決 ∪ judgment_history 快照，distinct 非空）。

    供前端「判決模型」篩選與導出「輸出結果版本」下拉選項。字母序；`stub`（無 key 假判）
    降權排最後——保留而非隱藏，否則純 stub 環境下拉空白、功能整支失效。
    """
    jg, h = T.judgments, T.judgment_history
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
                .where(h.c.kind == "judgment", h.c.model.isnot(None), h.c.model != "")
            )
        }
    return sorted(models, key=lambda m: (m == "stub", m))
