"""全庫資料包匯入端點（乾跑校驗 / 確認匯入背景 job / 進度 SSE）；全路徑自帶 /api。

安全（現階段）：
- 僅執行「純資料灌入白名單表」，不執行上傳內容為 SQL（見 db.datapack）。
- 匯入端點掛 `require_permission(data.datapack.import)`（admin 級）、導出掛 `data.datapack.export`（qc+admin）——
  權限經可替換框架 provider 判定（見 app/core/permissions），日後換 be2 零改此檔。
- 環境閘 `AIQ_ALLOW_DATA_IMPORT`：None＝依環境（development 開、其餘關），防生產誤觸（權限之外的第二道保險）。
- 破壞性動作需 type-to-confirm（confirm_phrase 由 datapack.CONFIRM_PHRASE 定義，validate 回傳給前端）。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from app.core import export_jobs, import_jobs
from app.core.config import env
from app.core.db import datapack
from app.core.permissions import permission_keys, require_permission

router = APIRouter()


def _import_allowed() -> bool:
    """匯入是否放行：顯式 AIQ_ALLOW_DATA_IMPORT 優先，未設則僅 development 放行。"""
    if env.aiq_allow_data_import is not None:
        return env.aiq_allow_data_import
    return env.app_env == "development"


def _guard() -> None:
    """環境閘：非放行環境回 403，避免生產誤觸整庫覆蓋。"""
    if not _import_allowed():
        raise HTTPException(
            status_code=403,
            detail=f"目前環境（APP_ENV={env.app_env}）未開放資料匯入；設 AIQ_ALLOW_DATA_IMPORT=true 才可用。",
        )


@router.post("/api/admin/import/validate")
async def validate_import(
    file: UploadFile = File(...),
    include_sensitive: bool = Form(False),
    _user: dict = Depends(require_permission(permission_keys.DATA_DATAPACK_IMPORT)),
) -> dict:
    """乾跑校驗資料包（零 DB 寫入）：回 schema 檢查 + 每表匯入計畫 + 需輸入的 confirm_phrase。

    前端據回傳畫預覽表（每表 will_truncate/will_insert）、schema_ok 綠/紅 banner、warnings 提示。
    """
    _guard()
    content = await file.read()
    return datapack.validate_datapack(content, include_sensitive=include_sensitive)


@router.post("/api/admin/import")
async def run_import(
    file: UploadFile = File(...),
    confirm_phrase: str = Form(...),
    include_sensitive: bool = Form(False),
    _user: dict = Depends(require_permission(permission_keys.DATA_DATAPACK_IMPORT)),
) -> dict:
    """確認匯入（背景 job）：核對 confirm_phrase → 註冊背景任務單交易 truncate-then-load → 回 {job_id}。

    load_datapack 內會**再校驗一次**（TOCTOU：/validate 與此呼叫間 DB 可能已變）；任一步失敗整體
    rollback，DB 維持原狀。前端以回傳 job_id 連 /stream 觀察逐表進度。
    """
    _guard()
    if confirm_phrase.strip() != datapack.CONFIRM_PHRASE:
        raise HTTPException(
            status_code=400,
            detail=f"確認短語不符（需輸入 {datapack.CONFIRM_PHRASE}）；此操作將清空並覆蓋整庫，請確認。",
        )
    content = await file.read()

    def _runner(ctx: import_jobs.ImportCtx) -> dict:
        return datapack.load_datapack(content, include_sensitive=include_sensitive, ctx=ctx)

    job_id = import_jobs.start_import(_runner)
    return {"job_id": job_id}


@router.get("/api/admin/import/stream")
async def import_stream(job_id: str) -> StreamingResponse:
    """SSE 推送匯入進度（免輪詢）：讀 in-mem 快照，每 ~0.6s 推一次，狀態達 done/error 即關閉。

    比照 inbound/exports SSE 慣例；job_id 為不可猜的能力 token（EventSource 無法帶 Authorization header）。
    """

    async def _events():
        while True:
            snap = import_jobs.get_job(job_id)
            if snap is None:
                yield f"event: error\ndata: {json.dumps({'detail': 'job 不存在'}, ensure_ascii=False)}\n\n"
                return
            yield f"data: {json.dumps(snap, ensure_ascii=False)}\n\n"
            if snap["status"] in ("done", "error"):
                return
            await asyncio.sleep(0.6)

    return StreamingResponse(
        _events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/api/admin/export/start")
def start_export_datapack(
    include_sensitive: bool = False,
    _user: dict = Depends(require_permission(permission_keys.DATA_DATAPACK_EXPORT)),
) -> dict:
    """啟動全庫資料包導出背景 job（逐表回報進度）→ 立即回 {job_id}。

    復用通用 export_jobs（同 xlsx 導出）：前端以 /api/exports/stream 追 SSE 進度、done 後
    /api/exports/download 取 zip blob。build_datapack 的 progress 回呼 → ctx.report + check（可停止）。
    read-only、非破壞性，登入即可。
    """

    def _builder(ctx: export_jobs.ExportCtx) -> bytes:
        def _report(done: int, total: int) -> None:
            ctx.report(done, total)
            ctx.check()  # 停止旗標已 set 時拋 Cancelled，收斂為 cancelled

        return datapack.build_datapack(include_sensitive=include_sensitive, progress=_report)

    fname = f"kkday-ai-quality-datapack-{datetime.now(timezone.utc):%Y%m%d%H%M}.zip"
    return {"job_id": export_jobs.start_export(_builder, fname)}
