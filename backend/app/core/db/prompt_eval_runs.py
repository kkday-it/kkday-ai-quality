"""Prompt 測試歷史（prompt_eval_runs）：B2——每次 `prompt_eval.run_eval` 完成落一列結果快照。

與 judgment_runs（批次任務執行狀態機）不同：本表是單次同步評測的**結果快照**，落表即完成、
無執行中狀態，故只有 insert + 列表/詳情查詢，無狀態回寫。供「改 prompt 前後對比」——同一
prompt_id 依時間查歷次結果，指標/分歧逐輪可比。
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy import insert as sa_insert

from app.core.db import tables as T


def insert_prompt_eval_run(row: dict) -> str:
    """落一列測試結果快照，回傳 run_id（呼叫端不需先準備 id）。

    Args:
        row: {prompt_id, prompt_version, source, n, filters, metrics, mismatches, model, triggered_by}。

    Returns:
        新建列的 run_id。
    """
    run_id = f"peval_{uuid.uuid4().hex}"
    with T.get_engine().begin() as c:
        c.execute(
            sa_insert(T.prompt_eval_runs).values(
                run_id=run_id,
                prompt_id=row.get("prompt_id", ""),
                prompt_version=row.get("prompt_version"),
                source=row.get("source", "production"),
                n=row.get("n", 0),
                filters=row.get("filters"),
                metrics=row.get("metrics") or {},
                mismatches=row.get("mismatches") or [],
                model=row.get("model", ""),
                triggered_by=row.get("triggered_by", ""),
            )
        )
    return run_id


def list_prompt_eval_runs(prompt_id: str, limit: int = 20, offset: int = 0) -> dict:
    """某支 prompt 的測試歷史列表（created_at 降冪分頁）→ {total, items}；items 不含 mismatches
    （逐案分歧體積可觀，列表只列指標摘要，詳情走 `prompt_eval_run_detail`）。
    """
    r = T.prompt_eval_runs
    cols = (
        r.c.run_id,
        r.c.prompt_id,
        r.c.prompt_version,
        r.c.source,
        r.c.n,
        r.c.metrics,
        r.c.model,
        r.c.triggered_by,
        r.c.created_at,
    )
    stmt = select(*cols).where(r.c.prompt_id == prompt_id).order_by(r.c.created_at.desc())
    cnt = select(func.count()).select_from(r).where(r.c.prompt_id == prompt_id)
    with T.get_engine().connect() as c:
        total = int(c.execute(cnt).scalar() or 0)
        rows = c.execute(stmt.limit(limit).offset(offset)).mappings().all()
    return {"total": total, "items": [_serialize(dict(row)) for row in rows]}


def prompt_eval_run_detail(run_id: str) -> dict | None:
    """單一測試 run 完整詳情（含 filters/mismatches 逐案分歧）；不存在回 None。"""
    r = T.prompt_eval_runs
    with T.get_engine().connect() as c:
        row = c.execute(select(r).where(r.c.run_id == run_id)).mappings().first()
    return _serialize(dict(row)) if row is not None else None


def _serialize(row: dict) -> dict:
    """datetime 欄 → ISO 字串（對齊專案時間欄以字串出 API 的慣例）。"""
    v = row.get("created_at")
    row["created_at"] = v.isoformat() if v is not None and hasattr(v, "isoformat") else v
    return row
