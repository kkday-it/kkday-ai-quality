"""歸因歷史（prejudge_runs）：run 級寫入/回寫 + 列表分頁 + llm_usage per-stage 明細聚合。

一次「觸發 LLM 歸因」（批量初判 / 選取多筆 / 單筆重新初判）＝一列 run。寫入點＝prejudge_batch
（start_job 建檔 → 暫停/恢復/停止回寫狀態 → 終態回寫統計）；讀取供 /api/v1/prejudge/runs。
執行中的即時進度以 in-mem job 快照 overlay（API 層做），本模組只管持久化事實。
"""

from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy import insert as sa_insert
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.db import tables as T
from app.core.db._shared import select_wire, wire_row

# run 出 API 的欄白名單 {wire 鍵: DB 欄名}（當前為恆等映射）。
# 與 `tests/test_wire_contract.py` 的 `_PREJUDGE_RUN_WIRE` 成對，改一邊必改另一邊。
_WIRE_COLS = {
    "job_id": "job_id",
    "kind": "kind",
    "rejudge": "rejudge",
    "source": "source",
    "model": "model",
    "params": "params",
    "status": "status",
    "total": "total",
    "processed": "processed",
    "ok": "ok",
    "failed": "failed",
    "total_tokens": "total_tokens",
    "cost_usd": "cost_usd",
    "triggered_by": "triggered_by",
    "started_at": "started_at",
    "finished_at": "finished_at",
}

# 建檔時允許寫入的欄（其餘由 DB default 補；終態統計走 finish_prejudge_run）。
_INSERT_COLS = (
    "job_id",
    "kind",
    "rejudge",
    "source",
    "model",
    "params",
    "status",
    "total",
    "triggered_by",
)


def insert_prejudge_run(row: dict) -> None:
    """建立 run 紀錄（job 啟動時；status 由呼叫端帶 running）。"""
    vals = {k: row.get(k) for k in _INSERT_COLS}
    with T.get_engine().begin() as c:
        c.execute(sa_insert(T.prejudge_runs).values(**vals))


def update_prejudge_run_status(job_id: str, status: str) -> None:
    """回寫 run 狀態（暫停/恢復/停止中；終態走 finish_prejudge_run 連同統計一起回寫）。"""
    with T.get_engine().begin() as c:
        c.execute(
            update(T.prejudge_runs).where(T.prejudge_runs.c.job_id == job_id).values(status=status)
        )


def finish_prejudge_run(job_id: str, snap: dict) -> None:
    """終態回寫：狀態 + 進度統計 + token/費用 + finished_at（取 job 結束時的 in-mem 快照）。"""
    with T.get_engine().begin() as c:
        c.execute(
            update(T.prejudge_runs)
            .where(T.prejudge_runs.c.job_id == job_id)
            .values(
                status=snap.get("status", "done"),
                processed=snap.get("processed", 0),
                ok=snap.get("ok", 0),
                failed=snap.get("failed", 0),
                total_tokens=snap.get("total_tokens", 0),
                cost_usd=snap.get("cost_usd", 0.0),
                finished_at=func.now(),
            )
        )


def save_run_log_item(
    job_id: str, source_id: str, entries: list[dict], triggered_by: str = ""
) -> None:
    """落存**單一評論**的初判執行日誌（該筆判完即寫，不等整批結束）。

    一筆一列 INSERT（衝突則覆蓋，供重跑同一筆時取代舊日誌）——刻意不累加回 prejudge_runs 的
    JSONB 欄：那會讓每筆都整列重寫一次已成長的 blob，34k 筆的批次累計寫入量是 O(N²)。
    `source_id` 空字串＝job 級事件（任務啟動/收尾）。
    """
    lg = T.prejudge_run_logs
    stmt = pg_insert(lg).values(
        job_id=job_id, source_id=source_id, entries=entries, create_user=triggered_by or None
    )
    with T.get_engine().begin() as c:
        c.execute(
            stmt.on_conflict_do_update(
                index_elements=[lg.c.job_id, lg.c.source_id],
                set_={"entries": stmt.excluded.entries, "create_date": func.now()},
            )
        )


def get_run_log(job_id: str, source_id: str | None = None, merge_limit: int = 20) -> dict | None:
    """讀某 job 的執行日誌 → {entries, items, truncated}；完全沒有日誌回 None（端點轉 404）。

    Args:
        job_id: 初判任務 id。
        source_id: 只看這則評論（附 job 級事件供脈絡）；None＝整批視角。
        merge_limit: 整批視角最多合併幾則評論的條目（大批量下全合併會撐爆前端渲染；
            未納入的評論仍列在 items 供逐則點選）。

    Returns:
        entries: 依 ts 遞增排序的條目陣列。
        items: 本 job 有日誌的評論清單 [{source_id, count}]（不含 job 級列）。
        truncated: 整批視角下是否有評論未併入 entries。
    """
    lg = T.prejudge_run_logs
    with T.get_engine().connect() as c:
        rows = (
            c.execute(
                select(lg.c.source_id, lg.c.entries)
                .where(lg.c.job_id == job_id)
                .order_by(lg.c.prejudge_run_log_oid)  # 落庫序＝評論判完的先後
            )
            .mappings()
            .all()
        )
    if not rows:
        return None
    job_level = [r for r in rows if not r["source_id"]]
    items = [
        {"source_id": r["source_id"], "count": len(r["entries"])} for r in rows if r["source_id"]
    ]
    if source_id is not None:
        picked = [r for r in rows if r["source_id"] == source_id]
        if not picked and not job_level:
            return None
        truncated = False
    else:
        picked = [r for r in rows if r["source_id"]][:merge_limit]
        truncated = len(items) > len(picked)
    entries = [e for r in (*job_level, *picked) for e in r["entries"]]
    entries.sort(key=lambda e: e.get("ts") or 0)
    return {"entries": entries, "items": items, "truncated": truncated}


def any_judged(source: str | None, item_ids: list[str], sample_cap: int = 1000) -> bool:
    """標的中是否已有初判（判定本次為「重新初判」）；超大清單只抽前 sample_cap 筆探測（夠準且省查詢）。"""
    ids = [str(i) for i in item_ids[:sample_cap] if i]
    if not ids or not source:
        return False
    j = T.attributions
    with T.get_engine().connect() as c:
        row = c.execute(
            select(j.c.attribution_oid).where(j.c.source == source, j.c.source_id.in_(ids)).limit(1)
        ).first()
    return row is not None


def list_prejudge_runs(limit: int = 20, offset: int = 0, source: str | None = None) -> dict:
    """歸因歷史列表（started_at 降冪分頁）→ {total, items}；datetime 轉 ISO 字串。"""
    r = T.prejudge_runs
    stmt = select_wire(r).order_by(r.c.started_at.desc())
    cnt = select(func.count()).select_from(r)
    if source:
        stmt = stmt.where(r.c.source == source)
        cnt = cnt.where(r.c.source == source)
    with T.get_engine().connect() as c:
        total = int(c.execute(cnt).scalar() or 0)
        rows = c.execute(stmt.limit(limit).offset(offset)).mappings().all()
    return {"total": total, "items": [_serialize(row) for row in rows]}


def prejudge_run_detail(job_id: str) -> dict | None:
    """單一 run 詳情：run 欄位 + llm_usage per-stage 明細聚合（stages；job 結束 flush 後才有值）。"""
    r = T.prejudge_runs
    u = T.llm_usage
    with T.get_engine().connect() as c:
        row = c.execute(select_wire(r).where(r.c.job_id == job_id)).mappings().first()
        if row is None:
            return None
        stages = [
            {
                "stage": s["stage"] or "（未標）",
                "calls": int(s["n"]),
                "prompt_tokens": int(s["p"] or 0),
                "completion_tokens": int(s["c"] or 0),
                "reasoning_tokens": int(s["r"] or 0),
                "cached_tokens": int(s["ca"] or 0),
                "cost_usd": round(float(s["cost"] or 0.0), 6),
            }
            for s in c.execute(
                select(
                    u.c.stage,
                    func.count().label("n"),
                    func.sum(u.c.prompt_tokens).label("p"),
                    func.sum(u.c.completion_tokens).label("c"),
                    func.sum(u.c.reasoning_tokens).label("r"),
                    func.sum(u.c.cached_tokens).label("ca"),
                    func.sum(u.c.cost_usd).label("cost"),
                )
                .where(u.c.job_id == job_id)
                .group_by(u.c.stage)
                .order_by(func.sum(u.c.cost_usd).desc())
            )
            .mappings()
            .all()
        ]
    return {**_serialize(row), "stages": stages}


def _serialize(row) -> dict:
    """run 列 → API dict（顯式白名單 + datetime 轉 ISO 字串）。

    白名單使 DB 新增欄不會自動流進 API（見 `_shared.wire_row`）。執行日誌另存
    `prejudge_run_log_lst`（一評論一列），專用 `get_run_log` 才讀，不隨 run 摘要/詳情回傳。
    """
    return wire_row(row, _WIRE_COLS)
