"""通用導出 job 端點：進度串流 / 停止 / 取檔（跨領域共用）。

導出 job registry（app.core.export_jobs）為全域 in-mem，故 SSE 串流、停止、下載三端點與導出「內容」
無關、可被任何導出（問題列表 / 初判規則 / 未來新增）共用；各領域只需自己的 start 端點呼叫
`export_jobs.start_export(builder, filename)` 取得 job_id，其餘生命週期都走這裡。

契約對齊 frontend/apps/console/src/api/exports.api.ts（exportStreamUrl / cancelExport / downloadExport）。
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse

from app.core import auth, export_jobs

router = APIRouter(prefix="/api/exports", tags=["exports"])

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
# 依下載檔名副檔名決定 media_type（各領域 builder 產不同格式：xlsx 結構表 / zip prompt 包 / csv）。
_MIME_BY_EXT = {".xlsx": _XLSX_MIME, ".zip": "application/zip", ".csv": "text/csv"}


def _mime_for(name: str) -> str:
    """由下載檔名副檔名取對應 MIME；未知副檔名回 xlsx（歷史預設，最常見）。"""
    for ext, mime in _MIME_BY_EXT.items():
        if name.lower().endswith(ext):
            return mime
    return _XLSX_MIME


@router.get("/stream")
async def export_stream(job_id: str) -> StreamingResponse:
    """SSE 長連線推送導出進度（免前端輪詢）：每 ~0.5s 推一次快照，job done/error/cancelled 即關閉。

    不加 auth Depends：原生 EventSource 無法帶 Authorization header；job_id 為不可猜的隨機
    capability token（僅發起導出的登入者取得），以其本身作為存取憑證（與 prejudge/上傳 SSE 一致）。
    """

    async def _events():
        """快照 → SSE event 產生器；job 不存在推 error、終態推完即 return 結束串流。"""
        while True:
            snap = export_jobs.get_job(job_id)
            if snap is None:
                yield f"event: error\ndata: {json.dumps({'detail': 'job 不存在'}, ensure_ascii=False)}\n\n"
                return
            yield f"data: {json.dumps(snap, ensure_ascii=False)}\n\n"
            if snap["status"] in ("done", "error", "cancelled"):
                return
            await asyncio.sleep(0.5)

    return StreamingResponse(
        _events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/cancel")
def export_cancel(job_id: str, _: dict = Depends(auth.get_current_user)) -> dict:
    """停止導出 job（builder 下個 check 點收斂 → cancelled，不產出檔案）→ 回更新後快照。"""
    if not export_jobs.cancel_export(job_id):
        raise HTTPException(status_code=409, detail=f"job 不存在或已結束，無法停止：{job_id}")
    return export_jobs.get_job(job_id) or {}


@router.get("/download")
def export_download(
    job_id: str, filename: str | None = None, _: dict = Depends(auth.get_current_user)
) -> Response:
    """取回已完成導出 job 的位元組（attachment，media_type 依檔名副檔名判定：xlsx/zip/csv）；
    一次性，取後即清 job 與結果。

    Args:
        filename: 覆寫下載檔名（前端以本地時間戳命名）；缺省用 job 快照登記的檔名。
    """
    snap = export_jobs.get_job(job_id)
    if snap is None:
        raise HTTPException(status_code=404, detail=f"job 不存在或已取走：{job_id}")
    if snap["status"] != "done":
        raise HTTPException(
            status_code=409, detail=f"job 尚未完成（status={snap['status']}），無法下載"
        )
    data = export_jobs.pop_result(job_id)
    if data is None:
        raise HTTPException(status_code=404, detail="結果已取走或不存在")
    name = filename or snap.get("filename") or "export.xlsx"
    return Response(
        content=data,
        media_type=_mime_for(name),
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
