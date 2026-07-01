"""初判歸因端點：前端發起批量判決 + 進度輪詢。

契約逐欄對齊 frontend/apps/console/src/api/judgment.api.ts（startPrejudge / getPrejudgeStatus）。
判決本體在 app/judge/prejudge_batch（背景 ThreadPool），本層只負責標的解析 + 設定注入 + job 轉發。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core import auth, db
from app.core import settings as app_settings
from app.judge import prejudge_batch

router = APIRouter(prefix="/judgment", tags=["judgment"])


class PrejudgeIn(BaseModel):
    """初判歸因請求：item_ids 顯式選取優先；否則 scope=all 取該來源全部未判。"""

    item_ids: list[str] | None = None
    source: str | None = None
    scope: str | None = None  # "all"＝全部未判（item_ids 未給時生效）
    llm_config_id: str | None = None  # 指定已存 LLM 配置（缺＝active）


@router.post("/prejudge")
def start_prejudge(body: PrejudgeIn, user: dict = Depends(auth.get_current_user)) -> dict:
    """啟動初判歸因批量任務 → {job_id, total, model}（立即回，背景派工）。

    標的解析：item_ids 顯式 > scope=="all" 取 db.unjudged_item_ids(source) > 空集合。
    設定注入：以當前 user 的 effective LLM dict（可選 llm_config_id）供 judge 路徑跨 thread 讀取。
    """
    uid = user.get("user_id", "")
    eff = app_settings.effective_llm_dict(app_settings.load_settings(uid), config_id=body.llm_config_id)
    model = eff.get("model", "")

    if body.item_ids:
        item_ids = body.item_ids
    elif body.scope == "all":
        item_ids = db.unjudged_item_ids(body.source)
    else:
        item_ids = []

    job_id = prejudge_batch.start_job(item_ids, eff, model)
    return {"job_id": job_id, "total": len(item_ids), "model": model}


@router.get("/prejudge/status")
def prejudge_status(job_id: str, _: dict = Depends(auth.get_current_user)) -> dict:
    """查初判歸因任務進度 → {status, total, processed, ok, failed, model, total_tokens, cost_usd}。"""
    snap = prejudge_batch.get_job(job_id)
    if snap is None:
        raise HTTPException(status_code=404, detail=f"job 不存在或已清除：{job_id}")
    return snap
