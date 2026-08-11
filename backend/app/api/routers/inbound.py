"""資料錄入端點（乾跑校驗 / 確認上傳背景 job / 進度 SSE / 批次清單）；全路徑自帶 /api。"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core import auth, db
from app.core import source_mapping as srcmap
from app.core.db import source_registry
from app.core.permissions import permission_keys, require_permission
from app.judge.ingest import entry, upload_batch

from ._sse import job_progress_stream

router = APIRouter()


class UploadSelection(BaseModel):
    """確認匯入時用戶勾選的工作表：sheet_name + 確認來源（通常＝自動辨識結果）+ 用戶備註。"""

    sheet_name: str
    source: str
    note: str = ""


@router.post("/api/inbound/validate")
async def validate_inbound(
    file: UploadFile = File(...),
    _user: dict = Depends(require_permission(permission_keys.DATA_SOURCE_UPLOAD)),
) -> dict:
    """乾跑校驗（不落庫）：逐工作表自動辨識來源 + 必備表頭校驗，回每表能否上傳。需 data.source.upload 權限。

    支援多工作表 xlsx（一次傳整本 ai_judge_source.xlsx）；CSV 視為單表。
    前端據此彈窗：哪些表偵測到哪個來源、哪些可傳、哪些不可（缺哪些必備欄）。
    """
    content = await file.read()
    try:
        sheets = entry.read_sheets(content, file.filename or "")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    report = []
    for sh in sheets:
        headers = sh["headers"]
        src = srcmap.detect_source(headers)
        if src is None:
            report.append(
                {
                    "sheet_name": sh["sheet_name"],
                    "detected_source": None,
                    "label": "",
                    "status": "unknown",
                    "missing_headers": [],
                    "unmapped_headers": [],
                    "row_count": len(sh["rows"]),
                    "reason": "表頭無法對應任何已知來源（非 5 反饋源，略過）",
                }
            )
            continue
        missing = srcmap.validate_headers(src, headers)
        # 對不上任何 DB 欄的表頭：不擋上傳（上游多給欄位很正常），但必須在上傳「前」講清楚
        # ——否則那些欄會靜默不落庫，事後只看得到「匯入成功但該欄全空」。
        column_map = source_registry.header_column_map(src)
        unmapped = sorted(
            {
                str(h).strip()
                for h in headers
                if h is not None and str(h).strip() and str(h).strip() not in column_map
            }
        )
        report.append(
            {
                "sheet_name": sh["sheet_name"],
                "detected_source": src,
                "label": srcmap.source_label(src),
                "status": "ok" if not missing else "fail",
                "missing_headers": missing,
                "unmapped_headers": unmapped,
                "row_count": len(sh["rows"]),
                "reason": "" if not missing else f"缺必備欄：{'、'.join(missing)}",
            }
        )
    return {"filename": file.filename, "sheets": report}


@router.post("/api/inbound/upload")
async def upload_inbound(
    file: UploadFile = File(...),
    selections: str = Form(...),
    _user: dict = Depends(require_permission(permission_keys.DATA_SOURCE_UPLOAD)),
) -> dict:
    """確認匯入（背景 job）：解析 + 校驗勾選工作表 → 註冊背景任務逐表分塊落庫 → 立即回 {job_id, sheets}。

    selections：JSON 字串 `[{"sheet_name","source"}]`（來自 /validate 後用戶勾選）。
    大檔（數萬列）改走背景 job + 前端輪詢 `/api/inbound/upload/status` 畫每表進度條；逐列容錯、
    壞列跳過並回報原因。5 來源統一：原始源列直接 upsert 各自來源專表（見 `upload_batch.py`）。
    """
    content = await file.read()
    try:
        sel = [UploadSelection(**s).model_dump() for s in json.loads(selections)]
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=f"selections 格式錯誤：{e}") from None
    if not sel:
        raise HTTPException(status_code=400, detail="未選擇任何工作表")
    return upload_batch.start_upload_job(content, file.filename or "", sel)


@router.get("/api/inbound/upload/stream")
async def upload_inbound_stream(job_id: str) -> StreamingResponse:
    """SSE 長連線推送上傳進度（免前端輪詢）：伺服器讀 in-mem 快照，每 ~0.6s 推一次 event，job 結束即關閉。

    單向 server→client 進度推送用 SSE 最貼切（不需 WebSocket 雙向）；`X-Accel-Buffering: no`
    關閉 nginx 緩衝確保即時。前端以原生 EventSource 接收、status≠running 時關閉連線。
    """

    return job_progress_stream(lambda: upload_batch.get_job(job_id), interval=0.6)


@router.get("/api/batches")
def get_batches(_: dict = Depends(auth.get_current_user)) -> list[dict]:
    """上傳批次清單（新到舊）。"""
    return db.list_batches()
