"""售後根因 ad-hoc Prompt 調試端點：任意文字 → 即時結構化裁決（SSE，不落 attributions）。

自 v1/prejudge.py 拆出（2026-07-23，原檔混三領域違反一 router 一領域慣例）；
LlmOverridesIn 重用 prejudge.py 的共用契約，不另立第三個共用模組。

批量跑批（/prompt-debug/batch/*）：上傳 CSV/XLSX 以當前 Prompt/契約整批裁決，斷點續跑；
本體在 app/judge/prompt_debug_batch（背景 ThreadPool + run 目錄落 data/），本層只做
multipart 解析 + 設定注入 + job 轉發，進度走輪詢（同 prompt_sandbox 慣例）。
"""

from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.core import auth
from app.core import settings as app_settings
from app.core.permissions import permission_keys, require_permission

from .prejudge import LlmOverridesIn

router = APIRouter(prefix="/prejudge", tags=["prompt-debug"])

# 上傳輸入檔大小上限：調試台定位百~數千條對話（19MB 全量 xlsx 實測遠低於此），防誤傳超大檔撐爆磁碟
_MAX_UPLOAD_BYTES = 64 * 1024 * 1024


class PromptDebugIn(BaseModel):
    """任意售後對話 Prompt 調試請求。"""

    text: str = Field(min_length=1, max_length=200_000)
    system_prompt: str = Field(min_length=1, max_length=300_000)
    # 輸出契約版本：v2=現行批次同款；v3=新規格（keywords 陣列/urgency 1–5/no_actionable_content/n-a 哨兵）。
    # 貼什麼契約的 Prompt 就選什麼——schema 與欄位校驗隨之切換，否則 strict schema 會把輸出扭回另一套欄位。
    contract: Literal["v2", "v3"] = "v2"
    overrides: LlmOverridesIn | None = None  # 本次臨時旋鈕覆寫；缺省沿用 prompt_debug 功能區默認


@router.get("/prompt-debug/defaults")
def prompt_debug_defaults(user: dict = Depends(auth.get_current_user)) -> dict:
    """回傳 Google Doc 分類庫渲染的預設 Prompt、schema 與裁判表摘要。"""
    from app.judge import prompt_debug

    return prompt_debug.defaults_payload()


def _effective_or_400(overrides: dict | None) -> dict:
    """解析 prompt_debug 功能區 effective LLM dict；缺 token / model 即 400（單次與批量共用）。

    Raises:
        HTTPException: 400，配置解不出 API token 或未指定 model。
    """
    saved = app_settings.load_settings()
    effective = app_settings.effective_llm_dict(saved, area="prompt_debug", overrides=overrides)
    if not app_settings.resolve_provider_token(effective):
        raise HTTPException(
            status_code=400,
            detail="目前配置沒有可用 API token，請先在「配置 › LLM 模型連線」完成設定",
        )
    if not (effective.get("model") or "").strip():
        raise HTTPException(status_code=400, detail="本次調試未指定 model")
    return effective


@router.post("/prompt-debug/stream")
def prompt_debug_stream(
    body: PromptDebugIn,
    user: dict = Depends(require_permission(permission_keys.PREJUDGE_RUN)),
) -> StreamingResponse:
    """以 SSE 串流任意文字的結構化裁決、欄位校驗與本次 token/費用。"""
    from app.judge import prompt_debug

    overrides = body.overrides.model_dump(exclude_unset=True) if body.overrides else None
    effective = _effective_or_400(overrides)

    return StreamingResponse(
        prompt_debug.stream_frames(
            body.text, body.system_prompt, effective, contract=body.contract
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── 批量跑批（上傳檔 × 當前 Prompt/契約 → 斷點續跑批次）───────────────────────────


class PromptDebugBatchResumeIn(BaseModel):
    """續跑/重跑請求：workers 缺省沿用 manifest；rerun=true 忽略斷點全部重打。"""

    workers: int | None = Field(default=None, ge=1)
    rerun: bool = False


def _parse_overrides_form(overrides: str) -> dict | None:
    """multipart 無巢狀結構，LLM 覆寫以 JSON 字串傳遞；此處驗形回 dict（空字串＝無覆寫）。

    Raises:
        HTTPException: 400，overrides 不是合法 JSON 或形狀不符 LlmOverridesIn。
    """
    if not overrides.strip():
        return None
    try:
        model = LlmOverridesIn.model_validate(json.loads(overrides))
    except Exception as exc:  # noqa: BLE001 - JSON/形狀錯誤統一轉 400
        raise HTTPException(status_code=400, detail=f"overrides 參數不合法：{exc}") from exc
    return model.model_dump(exclude_unset=True)


@router.post("/prompt-debug/batch/start")
async def prompt_debug_batch_start(
    file: UploadFile = File(..., description="輸入 .csv/.xlsx/.xlsm（含 id 與對話欄）"),
    system_prompt: str = Form(...),
    contract: Literal["v2", "v3"] = Form("v2"),
    sheet: str = Form("", description="XLSX 工作表名；空＝第一個工作表"),
    id_column: str = Form("session_oid"),
    text_column: str = Form("conversation_full"),
    offset: int = Form(0, ge=0),
    limit: int = Form(0, ge=0, description="實際跑多少條；0＝全部"),
    workers: int = Form(8, ge=1),
    overrides: str = Form("", description="LlmOverridesIn 形狀的 JSON 字串；空＝沿用功能區默認"),
    user: dict = Depends(require_permission(permission_keys.PREJUDGE_RUN)),
) -> dict:
    """啟動批量跑批：存輸入檔 + 當前 Prompt 進 run 目錄，背景整批裁決；回初始進度快照（含 run_id）。"""
    from app.judge import prompt_debug_batch

    overrides_dict = _parse_overrides_form(overrides)
    effective = _effective_or_400(overrides_dict)
    input_bytes = await file.read()
    if len(input_bytes) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="輸入檔超過 64MB 上限")
    if not input_bytes:
        raise HTTPException(status_code=400, detail="輸入檔是空的")
    try:
        return prompt_debug_batch.create_and_start(
            input_name=file.filename or "input.csv",
            input_bytes=input_bytes,
            sheet=sheet,
            id_column=id_column.strip() or "session_oid",
            text_column=text_column.strip() or "conversation_full",
            offset=offset,
            limit=limit,
            workers=workers,
            contract=contract,
            system_prompt=system_prompt,
            overrides=overrides_dict,
            effective=effective,
            triggered_by=user.get("email", ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/prompt-debug/batch/runs")
def prompt_debug_batch_runs(user: dict = Depends(auth.get_current_user)) -> dict:
    """全部跑批 run 摘要（新→舊；磁碟目錄為準、in-mem 快照 overlay 即時進度）。"""
    from app.judge import prompt_debug_batch

    return {"runs": prompt_debug_batch.list_runs()}


@router.get("/prompt-debug/batch/runs/{run_id}")
def prompt_debug_batch_status(run_id: str, user: dict = Depends(auth.get_current_user)) -> dict:
    """單 run 進度輪詢：執行中回 in-mem 快照；已收尾回磁碟 summary；重啟遺留為 interrupted。"""
    from app.judge import prompt_debug_batch

    try:
        snap = prompt_debug_batch.get_run(run_id)
    except ValueError as exc:  # 非法 run_id（路徑穿越防禦）
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if snap is None:
        raise HTTPException(status_code=404, detail=f"跑批 run 不存在：{run_id}")
    return snap


@router.post("/prompt-debug/batch/runs/{run_id}/cancel")
def prompt_debug_batch_cancel(
    run_id: str,
    user: dict = Depends(require_permission(permission_keys.PREJUDGE_RUN)),
) -> dict:
    """停止執行中 run（已完成筆保留為斷點，可事後續跑）。"""
    from app.judge import prompt_debug_batch

    if not prompt_debug_batch.cancel_run(run_id):
        raise HTTPException(status_code=409, detail="run 不在執行中（可能已收尾或不存在）")
    return {"ok": True}


@router.post("/prompt-debug/batch/runs/{run_id}/resume")
def prompt_debug_batch_resume(
    run_id: str,
    body: PromptDebugBatchResumeIn,
    user: dict = Depends(require_permission(permission_keys.PREJUDGE_RUN)),
) -> dict:
    """續跑（只補未成功筆）或強制重跑；LLM 覆寫重放 manifest 快照，token 取當前設定。"""
    from app.judge import prompt_debug_batch

    try:
        manifest = prompt_debug_batch.read_manifest(run_id)
        effective = _effective_or_400(manifest.get("overrides") or None)
        return prompt_debug_batch.resume_run(
            run_id,
            effective,
            workers=body.workers,
            rerun=body.rerun,
            triggered_by=user.get("email", ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/prompt-debug/batch/runs/{run_id}/files/{kind}")
def prompt_debug_batch_file(
    run_id: str,
    kind: Literal["csv", "jsonl", "preds", "input"],
    user: dict = Depends(auth.get_current_user),
) -> FileResponse:
    """下載 run 產物：csv=結果表、jsonl=逐筆原始紀錄（斷點）、preds=成功判定彙總、input=原輸入檔。"""
    from app.judge import prompt_debug_batch

    try:
        path, name, media = prompt_debug_batch.download_path(run_id, kind)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path, filename=name, media_type=media)
