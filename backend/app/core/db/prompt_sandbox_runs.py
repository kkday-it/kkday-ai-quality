"""歸因列表 Prompt 測試沙盒歷史（prompt_sandbox_runs）：每次沙盒測試完成落一列結果快照。

與 judgments/judgment_history/judgment_runs（正式初判管線）完全分離——沙盒測試不落正式判決，
只落此表，確保「測試歷史」與「正式歸因」互不干擾。本表是「M 筆 item × 勾選 prompt 子集」的
逐筆結果 + 完整 LLM log 快照，供事後回看當時測試跑了什麼、LLM 說了什麼。只有 insert + 列表/
詳情查詢，無狀態回寫。
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy import insert as sa_insert

from app.core.db import tables as T


def insert_sandbox_run(row: dict) -> str:
    """落一列沙盒測試結果快照，回傳 run_id（呼叫端不需先準備 id）。

    Args:
        row: {source, scope, item_ids, prompt_ids, item_count, results, log, model,
              triggered_by, job_id}。

    Returns:
        新建列的 run_id。
    """
    run_id = f"psbx_{uuid.uuid4().hex}"
    with T.get_engine().begin() as c:
        c.execute(
            sa_insert(T.prompt_sandbox_runs).values(
                run_id=run_id,
                source=row.get("source", ""),
                scope=row.get("scope", "single"),
                item_ids=row.get("item_ids") or [],
                prompt_ids=row.get("prompt_ids") or [],
                item_count=row.get("item_count", 0),
                results=row.get("results") or [],
                log=row.get("log") or [],
                model=row.get("model", ""),
                triggered_by=row.get("triggered_by", ""),
                job_id=row.get("job_id"),
            )
        )
    return run_id


def list_sandbox_runs(limit: int = 20, offset: int = 0) -> dict:
    """沙盒測試歷史列表（created_at 降冪分頁）→ {total, items}；items 不含 results/log
    （逐筆結果與 log 快照體積可觀，列表只列摘要，詳情走 `sandbox_run_detail`）。
    """
    r = T.prompt_sandbox_runs
    cols = (
        r.c.run_id,
        r.c.source,
        r.c.scope,
        r.c.item_ids,
        r.c.prompt_ids,
        r.c.item_count,
        r.c.model,
        r.c.triggered_by,
        r.c.created_at,
    )
    stmt = select(*cols).order_by(r.c.created_at.desc())
    cnt = select(func.count()).select_from(r)
    with T.get_engine().connect() as c:
        total = int(c.execute(cnt).scalar() or 0)
        rows = c.execute(stmt.limit(limit).offset(offset)).mappings().all()
    return {"total": total, "items": [_serialize(dict(row)) for row in rows]}


def sandbox_run_detail(run_id: str) -> dict | None:
    """單一沙盒測試 run 完整詳情（含 results 逐筆結果 + log 完整快照）；不存在回 None。"""
    r = T.prompt_sandbox_runs
    with T.get_engine().connect() as c:
        row = c.execute(select(r).where(r.c.run_id == run_id)).mappings().first()
    return _serialize(dict(row)) if row is not None else None


def _serialize(row: dict) -> dict:
    """datetime 欄 → ISO 字串（對齊專案時間欄以字串出 API 的慣例）。"""
    v = row.get("created_at")
    row["created_at"] = v.isoformat() if v is not None and hasattr(v, "isoformat") else v
    return row
