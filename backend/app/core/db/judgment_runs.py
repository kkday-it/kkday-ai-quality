"""歸因歷史（judgment_runs）：run 級寫入/回寫 + 列表分頁 + llm_usage per-stage 明細聚合。

一次「觸發 LLM 歸因」（批量初判 / 選取多筆 / 單筆重判）＝一列 run。寫入點＝prejudge_batch
（start_job 建檔 → 暫停/恢復/停止回寫狀態 → 終態回寫統計）；讀取供 /api/v1/judgment/runs。
執行中的即時進度以 in-mem job 快照 overlay（API 層做），本模組只管持久化事實。
"""

from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy import insert as sa_insert

from app.core.db import tables as T

# 建檔時允許寫入的欄（其餘由 DB default 補；終態統計走 finish_judgment_run）。
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


def insert_judgment_run(row: dict) -> None:
    """建立 run 紀錄（job 啟動時；status 由呼叫端帶 running）。"""
    vals = {k: row.get(k) for k in _INSERT_COLS}
    with T.get_engine().begin() as c:
        c.execute(sa_insert(T.judgment_runs).values(**vals))


def update_judgment_run_status(job_id: str, status: str) -> None:
    """回寫 run 狀態（暫停/恢復/停止中；終態走 finish_judgment_run 連同統計一起回寫）。"""
    with T.get_engine().begin() as c:
        c.execute(
            update(T.judgment_runs).where(T.judgment_runs.c.job_id == job_id).values(status=status)
        )


def finish_judgment_run(job_id: str, snap: dict) -> None:
    """終態回寫：狀態 + 進度統計 + token/費用 + finished_at（取 job 結束時的 in-mem 快照）。"""
    with T.get_engine().begin() as c:
        c.execute(
            update(T.judgment_runs)
            .where(T.judgment_runs.c.job_id == job_id)
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


def any_judged(source: str | None, item_ids: list[str], sample_cap: int = 1000) -> bool:
    """標的中是否已有判決（判定本次為「重判」）；超大清單只抽前 sample_cap 筆探測（夠準且省查詢）。"""
    ids = [str(i) for i in item_ids[:sample_cap] if i]
    if not ids or not source:
        return False
    j = T.judgments
    with T.get_engine().connect() as c:
        row = c.execute(
            select(j.c.finding_id).where(j.c.source == source, j.c.source_id.in_(ids)).limit(1)
        ).first()
    return row is not None


def list_judgment_runs(limit: int = 20, offset: int = 0, source: str | None = None) -> dict:
    """歸因歷史列表（started_at 降冪分頁）→ {total, items}；datetime 轉 ISO 字串。"""
    r = T.judgment_runs
    stmt = select(r).order_by(r.c.started_at.desc())
    cnt = select(func.count()).select_from(r)
    if source:
        stmt = stmt.where(r.c.source == source)
        cnt = cnt.where(r.c.source == source)
    with T.get_engine().connect() as c:
        total = int(c.execute(cnt).scalar() or 0)
        rows = c.execute(stmt.limit(limit).offset(offset)).mappings().all()
    return {"total": total, "items": [_serialize(dict(row)) for row in rows]}


def judgment_run_detail(job_id: str) -> dict | None:
    """單一 run 詳情：run 欄位 + llm_usage per-stage 明細聚合（stages；job 結束 flush 後才有值）。"""
    r = T.judgment_runs
    u = T.llm_usage
    with T.get_engine().connect() as c:
        row = c.execute(select(r).where(r.c.job_id == job_id)).mappings().first()
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
    return {**_serialize(dict(row)), "stages": stages}


def _serialize(row: dict) -> dict:
    """datetime 欄 → ISO 字串（對齊專案時間欄以字串出 API 的慣例）。"""
    for k in ("started_at", "finished_at"):
        v = row.get(k)
        row[k] = v.isoformat() if v is not None and hasattr(v, "isoformat") else v
    return row
