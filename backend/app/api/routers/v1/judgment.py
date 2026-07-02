"""初判歸因端點：前端發起批量判決 + 進度輪詢。

契約逐欄對齊 frontend/apps/console/src/api/judgment.api.ts（startPrejudge / getPrejudgeStatus）。
判決本體在 app/judge/prejudge_batch（背景 ThreadPool），本層只負責標的解析 + 設定注入 + job 轉發。
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
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
    product_verticals: list[str] | None = None  # 全局商品垂直分類（scope=all 時約束標的集合）


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
        item_ids = db.unjudged_item_ids(body.source, body.product_verticals)
    else:
        item_ids = []

    job_id = prejudge_batch.start_job(item_ids, eff, model, source=body.source)
    return {"job_id": job_id, "total": len(item_ids), "model": model}


@router.get("/prejudge/status")
def prejudge_status(job_id: str, _: dict = Depends(auth.get_current_user)) -> dict:
    """查初判歸因任務進度（輪詢後備；主推 /stream SSE）→ {status, total, processed, ok, failed, model, total_tokens, cost_usd}。"""
    snap = prejudge_batch.get_job(job_id)
    if snap is None:
        raise HTTPException(status_code=404, detail=f"job 不存在或已清除：{job_id}")
    return snap


@router.post("/prejudge/pause")
def pause_prejudge(job_id: str, _: dict = Depends(auth.get_current_user)) -> dict:
    """暫停執行中的初判歸因任務（提交迴圈阻塞、已在跑的收斂後 processed 停增）→ 回更新後快照。"""
    if not prejudge_batch.pause_job(job_id):
        raise HTTPException(status_code=409, detail=f"job 不存在或非執行中，無法暫停：{job_id}")
    return prejudge_batch.get_job(job_id) or {}


@router.post("/prejudge/resume")
def resume_prejudge(job_id: str, _: dict = Depends(auth.get_current_user)) -> dict:
    """恢復已暫停的初判歸因任務（提交迴圈續跑）→ 回更新後快照。"""
    if not prejudge_batch.resume_job(job_id):
        raise HTTPException(status_code=409, detail=f"job 不存在或非暫停中，無法恢復：{job_id}")
    return prejudge_batch.get_job(job_id) or {}


@router.post("/prejudge/cancel")
def cancel_prejudge(job_id: str, _: dict = Depends(auth.get_current_user)) -> dict:
    """停止初判歸因任務（不再派新工，已在跑的收斂後轉 cancelled）→ 回更新後快照。

    已判 finding 已即時落庫保留；欲繼續可對「剩餘未判」重新發起（scope=all）。
    """
    if not prejudge_batch.cancel_job(job_id):
        raise HTTPException(status_code=409, detail=f"job 不存在或已結束，無法停止：{job_id}")
    return prejudge_batch.get_job(job_id) or {}


@router.get("/prejudge/stream")
async def prejudge_stream(job_id: str) -> StreamingResponse:
    """SSE 長連線推送初判歸因進度（免前端輪詢）：每 ~0.8s 推一次快照，job done/error/cancelled 即關閉。

    不加 auth Depends：原生 EventSource 無法帶 Authorization header；job_id 為不可猜的隨機
    capability token（僅發起判決的登入者取得），以其本身作為存取憑證（與上傳 SSE 一致）。
    `X-Accel-Buffering: no` 關 nginx 緩衝確保即時推送。
    """

    async def _events():
        """快照 → SSE event 產生器；job 不存在推 error、終態推完即 return 結束串流。"""
        while True:
            snap = prejudge_batch.get_job(job_id)
            if snap is None:
                yield f"event: error\ndata: {json.dumps({'detail': 'job 不存在'}, ensure_ascii=False)}\n\n"
                return
            yield f"data: {json.dumps(snap, ensure_ascii=False)}\n\n"
            if snap["status"] in ("done", "error", "cancelled"):
                return
            await asyncio.sleep(0.8)

    return StreamingResponse(
        _events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
