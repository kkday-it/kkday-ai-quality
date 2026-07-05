"""AI 使用紀錄（llm_usage）：per-call 寫入 + 消耗 dashboard 多維度聚合。

寫入由 llm.client.chat_json 的 usage recorder 呼叫（批次 bulk / 單次即時）；聚合供 /api/llm-usage/overview。
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy import insert as sa_insert

from app.core.db import tables as T

_GRAN_FMT = {"year": "YYYY", "month": "YYYY-MM", "day": "YYYY-MM-DD"}

# 寫入時只取這些欄（其餘由 DB default / autoincrement 補），避免呼叫端多帶鍵造成錯誤。
_INSERT_COLS = (
    "stage", "model", "provider",
    "prompt_tokens", "completion_tokens", "cached_tokens", "total_tokens",
    "cost_usd", "source", "source_id", "job_id",
)


def _clean(row: dict) -> dict:
    """只留 llm_usage 可寫欄位。"""
    return {k: row.get(k) for k in _INSERT_COLS}


def insert_llm_usage_row(row: dict) -> None:
    """寫入單筆 AI 使用紀錄（ad-hoc 單次 LLM 呼叫用）。"""
    with T.get_engine().begin() as c:
        c.execute(sa_insert(T.llm_usage).values(**_clean(row)))


def insert_llm_usage_rows(rows: list[dict]) -> int:
    """批量寫入 AI 使用紀錄（批次判決 job 結束時 flush）；回寫入列數。"""
    if not rows:
        return 0
    with T.get_engine().begin() as c:
        c.execute(sa_insert(T.llm_usage), [_clean(r) for r in rows])
    return len(rows)


def llm_usage_overview(
    date_from: str | None = None,
    date_to: str | None = None,
    granularity: str = "day",
) -> dict:
    """AI 消耗聚合：KPI + 每日趨勢 + 各模型/階段/來源分布（供消耗 dashboard）。

    created_at 為 timestamptz，用 to_char 分桶（day/month/year）；日期區間以格式化日字串比較（表小、簡明）。

    Returns:
        {kpi:{cost,tokens,calls,cached}, trend:[{bucket,cost,tokens,calls}],
         by_model/by_stage/by_source:[{key,cost,tokens,calls}]}。
    """
    u = T.llm_usage
    day = func.to_char(u.c.created_at, "YYYY-MM-DD")
    bucket = func.to_char(u.c.created_at, _GRAN_FMT.get(granularity, "YYYY-MM-DD"))
    cost = func.coalesce(func.sum(u.c.cost_usd), 0.0)
    toks = func.coalesce(func.sum(u.c.total_tokens), 0)
    cached = func.coalesce(func.sum(u.c.cached_tokens), 0)
    calls = func.count()

    def _filtered(stmt):
        if date_from:
            stmt = stmt.where(day >= date_from)
        if date_to:
            stmt = stmt.where(day <= date_to)
        return stmt

    def _grouped(dim):
        """依某維度欄聚合 [{key,cost,tokens,calls}]（依成本降冪；空 key→'（未標）'）。"""
        rows = c.execute(
            _filtered(
                select(func.coalesce(dim, "").label("key"), cost.label("c"), toks.label("t"), calls.label("n"))
                .group_by(func.coalesce(dim, ""))
                .order_by(cost.desc())
            )
        ).mappings().all()
        return [
            {"key": r["key"] or "（未標）", "cost": round(float(r["c"]), 6), "tokens": int(r["t"]), "calls": int(r["n"])}
            for r in rows
        ]

    with T.get_engine().connect() as c:
        krow = c.execute(
            _filtered(select(cost.label("c"), toks.label("t"), calls.label("n"), cached.label("ca")))
        ).mappings().first()
        kpi = {
            "cost": round(float(krow["c"]), 6),
            "tokens": int(krow["t"]),
            "calls": int(krow["n"]),
            "cached": int(krow["ca"]),
        }
        trend = [
            {"bucket": r["b"], "cost": round(float(r["c"]), 6), "tokens": int(r["t"]), "calls": int(r["n"])}
            for r in c.execute(
                _filtered(
                    select(bucket.label("b"), cost.label("c"), toks.label("t"), calls.label("n"))
                    .group_by(bucket).order_by(bucket.asc())
                )
            ).mappings().all()
        ]
        by_model = _grouped(u.c.model)
        by_stage = _grouped(u.c.stage)
        by_source = _grouped(u.c.source)

    return {"kpi": kpi, "trend": trend, "by_model": by_model, "by_stage": by_stage, "by_source": by_source}
