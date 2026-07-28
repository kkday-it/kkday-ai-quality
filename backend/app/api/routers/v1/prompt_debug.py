"""售後根因 ad-hoc Prompt 調試端點：任意文字 → 即時結構化裁決（SSE，不落 attributions）。

自 v1/prejudge.py 拆出（2026-07-23，原檔混三領域違反一 router 一領域慣例）；
LlmOverridesIn 重用 prejudge.py 的共用契約，不另立第三個共用模組。

批量跑批（/prompt-debug/batch/*）：上傳 CSV/XLSX 以當前 Prompt 整批裁決，斷點續跑；
本體在 app/judge/prompt_debug_batch（背景 ThreadPool + run 目錄落 data/），本層只做
multipart 解析 + 設定注入 + job 轉發，進度走輪詢（同 prompt_sandbox 慣例）。

人工評判案例庫（/prompt-debug/reviews）：把「AI 判錯了、正解是這個」逐案存進 `prompt_debug_reviews`，
供 AI 定點改寫當證據、供改完 Prompt 後整批回歸重跑。

AI 定點改寫（/prompt-debug/revise[/apply]）：拿選中案例餵旗艦模型（獨立的 `prompt_revise` 功能區，
與跑批用的便宜模型分開），SSE 串流回「診斷 + 補丁清單 + CHANGELOG 草稿」；`apply` 把勾選的補丁
套進全文回新內容（不落檔，要成為線上口徑仍走「存為新版本」）。本體在 app/judge/prompt_reviser。

回歸重跑（/prompt-debug/regression）：拿案例庫重跑候選 Prompt，逐欄比對「該修好的修好沒／該不動的
動了沒」。輕量 in-mem job（本體 app/judge/prompt_regression），進度走輪詢，後端重啟即清空。

Prompt 一律取版本庫最新版（`prompt_debug_versions`）：單次與批量的 system_prompt 都可留空
＝用最新版；頁面上臨時改過才送全文。「存為新版本」寫出新的時間戳檔，之後自動成為最新版。
"""

from __future__ import annotations

import json
from typing import Any, Literal

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
    # 留空＝用 Prompt 版本庫最新版；頁面上臨時編輯過才送全文
    system_prompt: str = Field(default="", max_length=300_000)
    overrides: LlmOverridesIn | None = None  # 本次臨時旋鈕覆寫；缺省沿用 prompt_debug 功能區默認


class PromptVersionIn(BaseModel):
    """存為新版本：把頁面上編輯後的全文寫成新的時間戳版本檔。"""

    system_prompt: str = Field(min_length=1, max_length=300_000)


@router.get("/prompt-debug/defaults")
def prompt_debug_defaults(user: dict = Depends(auth.get_current_user)) -> dict:
    """回傳最新版 Prompt、輸出 schema/欄位卡與裁判表摘要。"""
    from app.judge import prompt_debug

    return prompt_debug.defaults_payload()


@router.post("/prompt-debug/prompt-versions")
def prompt_debug_save_version(
    body: PromptVersionIn,
    user: dict = Depends(require_permission(permission_keys.JUDGE_RULE_MANAGE)),
) -> dict:
    """存為新版本；與最新版逐字相同時不建檔（回 created=false）。"""
    from app.judge import prompt_debug_versions

    try:
        saved = prompt_debug_versions.save(body.system_prompt)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {**saved, "versions": prompt_debug_versions.list_versions()}


# ── 人工評判案例庫（/prompt-debug/reviews）────────────────────────────────────


class PromptDebugReviewIn(BaseModel):
    """一則人工評判：AI 判了什麼、人認為哪幾欄錯、正解是什麼、有什麼修改建議。"""

    conversation: str = Field(min_length=1, max_length=200_000)
    ai_output: dict[str, Any]
    # 只放被標錯的欄；全欄皆對＝ {}（仍值得存，回歸時當正例防過度矯正）
    corrections: dict[str, Any] = Field(default_factory=dict)
    # 人明確標「對」的欄名；回歸時這些欄不准變（兩者都沒出現的欄＝沒看過，不計分）
    confirmed: list[str] = Field(default_factory=list)
    comment: str = Field(default="", max_length=10_000)
    prompt_version: str = Field(default="", max_length=64)
    model: str = Field(default="", max_length=200)


@router.get("/prompt-debug/reviews")
def prompt_debug_reviews(user: dict = Depends(auth.get_current_user)) -> dict:
    """案例庫列表（新→舊）；對話原文只回前 200 字預覽，全文由改寫/回歸端點按 id 取。"""
    from app.core import db

    return {"reviews": db.list_prompt_debug_reviews()}


@router.post("/prompt-debug/reviews", status_code=201)
def prompt_debug_review_create(
    body: PromptDebugReviewIn,
    user: dict = Depends(require_permission(permission_keys.PREJUDGE_RUN)),
) -> dict:
    """存一則人工評判案例。"""
    from app.core import db
    from app.judge import prompt_debug

    valid_keys = {field["key"] for field in prompt_debug.OUTPUT_FIELDS}
    unknown = sorted((set(body.corrections) | set(body.confirmed)) - valid_keys)
    if unknown:
        raise HTTPException(status_code=400, detail=f"不認得的欄位：{'、'.join(unknown)}")
    overlap = sorted(set(body.corrections) & set(body.confirmed))
    if overlap:
        raise HTTPException(status_code=400, detail=f"同一欄不能既標對又標錯：{'、'.join(overlap)}")

    review_id = db.insert_prompt_debug_review(
        conversation=body.conversation,
        ai_output=body.ai_output,
        corrections=body.corrections,
        confirmed=body.confirmed,
        comment=body.comment,
        prompt_version=body.prompt_version,
        model=body.model,
        reviewer=user.get("email", ""),
    )
    return {"id": review_id}


@router.delete("/prompt-debug/reviews/{review_id}")
def prompt_debug_review_delete(
    review_id: int,
    user: dict = Depends(require_permission(permission_keys.PREJUDGE_RUN)),
) -> dict:
    """刪一則案例。"""
    from app.core import db

    if not db.delete_prompt_debug_review(review_id):
        raise HTTPException(status_code=404, detail=f"案例不存在：{review_id}")
    return {"ok": True}


def _effective_or_400(overrides: dict | None, area: str = "prompt_debug") -> dict:
    """解析指定功能區的 effective LLM dict；缺 token / model 即 400。

    單次調試、批量跑批共用 `prompt_debug` 區（跑批要便宜）；AI 改寫走獨立的 `prompt_revise` 區
    （改 Prompt 要聰明，預設旗艦模型，見 config/global/llm_model.json areaDefaults）。

    Args:
        overrides: 本次臨時旋鈕覆寫；None＝全用該區默認。
        area: 功能區 key。

    Raises:
        HTTPException: 400，配置解不出 API token 或未指定 model。
    """
    saved = app_settings.load_settings()
    effective = app_settings.effective_llm_dict(saved, area=area, overrides=overrides)
    if not app_settings.resolve_provider_token(effective):
        raise HTTPException(
            status_code=400,
            detail="目前配置沒有可用 API token，請先在「配置 › LLM 模型連線」完成設定",
        )
    if not (effective.get("model") or "").strip():
        raise HTTPException(status_code=400, detail=f"「{area}」功能區未指定 model")
    return effective


@router.post("/prompt-debug/stream")
def prompt_debug_stream(
    body: PromptDebugIn,
    user: dict = Depends(require_permission(permission_keys.PREJUDGE_RUN)),
) -> StreamingResponse:
    """以 SSE 串流任意文字的結構化裁決、欄位校驗與本次 token/費用。"""
    from app.judge import prompt_debug, prompt_debug_versions

    overrides = body.overrides.model_dump(exclude_unset=True) if body.overrides else None
    effective = _effective_or_400(overrides)
    system_prompt, _version = prompt_debug_versions.resolve(body.system_prompt)

    return StreamingResponse(
        prompt_debug.stream_frames(body.text, system_prompt, effective),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── AI 定點改寫（案例 × 現行 Prompt → 補丁清單 → 套用）─────────────────────────


class PromptReviseIn(BaseModel):
    """依選中案例改寫 Prompt：system_prompt 留空＝用版本庫最新版。"""

    review_ids: list[int] = Field(min_length=1, max_length=50)
    system_prompt: str = Field(default="", max_length=300_000)
    overrides: LlmOverridesIn | None = None  # 缺省沿用 prompt_revise 功能區默認（旗艦模型）


class PromptPatchIn(BaseModel):
    """單條要套用的補丁；anchor 須為現行 Prompt 中唯一命中的逐字片段。"""

    anchor: str = Field(min_length=1)
    replacement: str = ""


class PromptReviseApplyIn(BaseModel):
    """把選定補丁套進全文。"""

    system_prompt: str = Field(min_length=1, max_length=300_000)
    patches: list[PromptPatchIn] = Field(min_length=1, max_length=32)


@router.post("/prompt-debug/revise")
def prompt_debug_revise(
    body: PromptReviseIn,
    user: dict = Depends(require_permission(permission_keys.PREJUDGE_RUN)),
) -> StreamingResponse:
    """以 SSE 串流旗艦模型產出的定點補丁（含 anchor 命中狀態）、診斷與 CHANGELOG 草稿。"""
    from app.core import db
    from app.judge import prompt_debug_versions, prompt_reviser

    cases = db.fetch_prompt_debug_reviews(body.review_ids)
    if not cases:
        raise HTTPException(status_code=404, detail="選中的案例都不存在（可能已被刪除）")

    overrides = body.overrides.model_dump(exclude_unset=True) if body.overrides else None
    effective = _effective_or_400(overrides, area="prompt_revise")
    system_prompt, _version = prompt_debug_versions.resolve(body.system_prompt)

    return StreamingResponse(
        prompt_reviser.stream_frames(system_prompt, cases, effective),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/prompt-debug/revise/apply")
def prompt_debug_revise_apply(
    body: PromptReviseApplyIn,
    user: dict = Depends(require_permission(permission_keys.PREJUDGE_RUN)),
) -> dict:
    """套用選定補丁，回套用後的全文（不落檔；要成為線上口徑仍須走「存為新版本」）。

    套用在後端而非前端：anchor 唯一性驗證與「由後往前替換避免位移」是正確性核心，
    兩邊各寫一份必然 drift。
    """
    from app.judge import prompt_reviser

    try:
        revised = prompt_reviser.apply_patches(
            body.system_prompt, [p.model_dump() for p in body.patches]
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "system_prompt": revised,
        "chars_before": len(body.system_prompt),
        "chars_after": len(revised),
    }


# ── 回歸重跑（案例庫 × 候選 Prompt → 修好/改壞逐欄比對）──────────────────────────


class PromptRegressionIn(BaseModel):
    """拿案例庫回歸驗證候選 Prompt；system_prompt 留空＝用版本庫最新版。"""

    review_ids: list[int] = Field(min_length=1, max_length=100)
    system_prompt: str = Field(default="", max_length=300_000)
    overrides: LlmOverridesIn | None = None  # 缺省沿用 prompt_debug 功能區默認（＝實際跑批的模型）


@router.post("/prompt-debug/regression")
def prompt_debug_regression_start(
    body: PromptRegressionIn,
    user: dict = Depends(require_permission(permission_keys.PREJUDGE_RUN)),
) -> dict:
    """啟動回歸重跑（背景），回初始進度快照（含 job_id）。

    模型走 `prompt_debug` 區而非改寫用的旗艦區——回歸要驗的是「這份 Prompt 在**實際跑批用的
    模型**上表現如何」，用更強的模型跑會得到偏樂觀、對不上線上的結論。
    """
    from app.core import db
    from app.judge import prompt_debug_versions, prompt_regression

    cases = db.fetch_prompt_debug_reviews(body.review_ids)
    if not cases:
        raise HTTPException(status_code=404, detail="選中的案例都不存在（可能已被刪除）")

    overrides = body.overrides.model_dump(exclude_unset=True) if body.overrides else None
    effective = _effective_or_400(overrides)
    system_prompt, _version = prompt_debug_versions.resolve(body.system_prompt)

    try:
        return prompt_regression.start(cases, system_prompt, effective)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/prompt-debug/regression/{job_id}")
def prompt_debug_regression_status(
    job_id: str, user: dict = Depends(auth.get_current_user)
) -> dict:
    """回歸進度輪詢；job 不存在（或後端重啟過）回 404。"""
    from app.judge import prompt_regression

    snap = prompt_regression.get_job(job_id)
    if snap is None:
        raise HTTPException(status_code=404, detail=f"回歸 job 不存在或已隨後端重啟清空：{job_id}")
    return snap


# ── 批量跑批（上傳檔 × 最新版 Prompt → 斷點續跑批次）─────────────────────────────


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
    system_prompt: str = Form("", description="空＝取 Prompt 版本庫最新版"),
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
