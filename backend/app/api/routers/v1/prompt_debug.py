"""售後根因 ad-hoc Prompt 調試端點：任意文字 → 即時結構化裁決（SSE，不落 attributions）。

自 v1/prejudge.py 拆出（2026-07-23，原檔混三領域違反一 router 一領域慣例）；
LlmOverridesIn 重用 prejudge.py 的共用契約，不另立第三個共用模組。

批量跑批（/prompt-debug/batch/*）：上傳 CSV/XLSX 以當前 Prompt 整批裁決，斷點續跑；
本體在 app/judge/prompt_debug_batch（背景 ThreadPool + run 目錄落 data/），本層只做
multipart 解析 + 設定注入 + job 轉發，進度走輪詢（同 prompt_sandbox 慣例）。
`/batch/start-multi` 為多模型並行入口：同一份輸入/Prompt 在多個 model 上各自獨立起一個
單模型 run，`/batch/groups/{group_id}` 供輪詢群組內各 model 的進度。

人工評判案例庫（/prompt-debug/reviews）：把「AI 判錯了、正解是這個」逐案存進 `prompt_debug_reviews`，
供 AI 定點改寫當證據、供改完 Prompt 後整批回歸重跑。

AI 定點改寫（/prompt-debug/revise[/apply]）：拿選中案例餵旗艦模型（獨立的 `prompt_revise` 功能區，
與跑批用的便宜模型分開），SSE 串流回「診斷 + 補丁清單 + CHANGELOG 草稿」；`apply` 把勾選的補丁
套進全文回新內容（不落檔，要成為線上口徑仍走「存為新草稿」→「設為正式版」）。本體在 app/judge/prompt_reviser。

回歸重跑（/prompt-debug/regression）：拿案例庫重跑候選 Prompt，逐欄比對「該修好的修好沒／該不動的
動了沒」。輕量 in-mem job（本體 app/judge/prompt_regression），進度走輪詢，後端重啟即清空。

Prompt 走草稿／正式版雙軌（`prompt_debug_versions`）：單次與批量的 system_prompt 都可留空
＝用**當前正式版**（active release，線上唯一口徑）；頁面上臨時改過才送全文。
「存為新草稿」寫進草稿區不影響線上；「設為正式版」才把某個草稿推成新的 active release。
**單次調試與跑批皆可認草稿**（`resolve(allow_draft=True)`，2026-07-30 起跑批不再硬拒草稿——
調試台是草稿工作台，草稿/正式版比例懸殊下硬拒等於跑批不可用；manifest 的 `prompt_kind` 顯式
記下本批跑的是 release／draft／臨時編輯，供事後回看「這批數據能不能當上線依據」）。
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
    # 留空＝用當前正式版；頁面上臨時編輯過才送全文
    system_prompt: str = Field(default="", max_length=300_000)
    overrides: LlmOverridesIn | None = None  # 本次臨時旋鈕覆寫；缺省沿用 prompt_debug 功能區默認


class PromptDraftIn(BaseModel):
    """存為新草稿：把頁面上編輯後的全文寫成新的時間戳草稿檔（不改變線上口徑）。"""

    system_prompt: str = Field(min_length=1, max_length=300_000)
    note: str = Field(default="", max_length=500)


class PromptReleaseIn(BaseModel):
    """升版：把某個既有草稿設為正式版（立即成為線上唯一口徑）。

    `name` 是人取的正式版名稱，走白名單（英數與 `. _ -`）——它會直接組成檔名，
    不能含 `/` 或 `..`。`note` 建議必填，供日後回顧「這版為何上線」。
    """

    draft: str = Field(min_length=1, max_length=64)
    # 首字元強制英數，與後端 `_RELEASE_RE` 同步（否則 `..` 會整串通過）
    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    note: str = Field(default="", max_length=500)


@router.get("/prompt-debug/drafts")
def prompt_debug_list_drafts(user: dict = Depends(auth.get_current_user)) -> dict:
    """草稿清單 + meta（新→舊）；不含全文。"""
    from app.judge import prompt_debug_versions

    return {"drafts": prompt_debug_versions.draft_meta()}


@router.get("/prompt-debug/releases")
def prompt_debug_list_releases(user: dict = Depends(auth.get_current_user)) -> dict:
    """正式版清單 + meta + 哪個 active；不含全文。"""
    from app.judge import prompt_debug_versions

    return {"releases": prompt_debug_versions.list_releases()}


@router.get("/prompt-debug/defaults")
def prompt_debug_defaults(user: dict = Depends(auth.get_current_user)) -> dict:
    """回傳當前正式版 Prompt、草稿/正式版清單、輸出 schema/欄位卡與裁判表摘要。"""
    from app.judge import prompt_debug

    return prompt_debug.defaults_payload()


@router.post("/prompt-debug/drafts")
def prompt_debug_save_draft(
    body: PromptDraftIn,
    user: dict = Depends(require_permission(permission_keys.PREJUDGE_RUN)),
) -> dict:
    """存為新草稿；與最新草稿逐字相同時不建檔（回 created=false）。**不改變線上口徑。**

    門檻刻意低於升版（`prejudge.run` vs `judge-rule.version.manage`）：存草稿是實驗行為、
    不影響任何人；把草稿推上線才是需要授權的動作。
    """
    from app.judge import prompt_debug_versions

    try:
        saved = prompt_debug_versions.save_draft(
            body.system_prompt, note=body.note, author=str(user.get("email") or "")
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {**saved, "drafts": prompt_debug_versions.draft_meta()}


@router.get("/prompt-debug/drafts/{version}")
def prompt_debug_get_draft(version: str, user: dict = Depends(auth.get_current_user)) -> dict:
    """取單一草稿全文（版本對比用）。"""
    from app.judge import prompt_debug_versions

    try:
        return {"version": version, "system_prompt": prompt_debug_versions.read_draft(version)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/prompt-debug/releases/{name}")
def prompt_debug_get_release(name: str, user: dict = Depends(auth.get_current_user)) -> dict:
    """取單一正式版全文（版本對比用）。"""
    from app.judge import prompt_debug_versions

    try:
        return {"name": name, "system_prompt": prompt_debug_versions.read_release(name)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/prompt-debug/releases")
def prompt_debug_promote_release(
    body: PromptReleaseIn,
    user: dict = Depends(require_permission(permission_keys.JUDGE_RULE_MANAGE)),
) -> dict:
    """把某個草稿升為正式版並立即成為線上口徑（跑批與調試台預設都改用它）。

    來源限定已存檔的草稿，不接受「編輯器當前內容」——升版要升的是可被 diff 與回查的那一份。
    """
    from app.judge import prompt_debug_versions

    try:
        result = prompt_debug_versions.promote(
            body.draft, body.name, note=body.note, author=str(user.get("email") or "")
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {**result, "releases": prompt_debug_versions.list_releases()}


@router.post("/prompt-debug/releases/{name}/activate")
def prompt_debug_activate_release(
    name: str,
    user: dict = Depends(require_permission(permission_keys.JUDGE_RULE_MANAGE)),
) -> dict:
    """把線上口徑切到某個**既有**正式版（回退／切換上線版本）。

    與升版（POST /releases）的分工：升版是「草稿 → 新正式版」（複製檔案 + 新增版本紀錄），
    本端點只改 active 指標，不複製檔案、不新增紀錄。升錯版時若沒有這條路，就只能再升一版
    （版本號無謂膨脹＋多一份內容重複的檔案）。
    """
    from app.judge import prompt_debug_versions

    try:
        result = prompt_debug_versions.set_active_release(name, author=str(user.get("email") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {**result, "releases": prompt_debug_versions.list_releases()}


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
    system_prompt, _version, _kind = prompt_debug_versions.resolve(
        body.system_prompt, allow_draft=True
    )

    return StreamingResponse(
        prompt_debug.stream_frames(body.text, system_prompt, effective),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── AI 定點改寫（案例 × 現行 Prompt → 補丁清單 → 套用）─────────────────────────


class PromptReviseIn(BaseModel):
    """依選中案例改寫 Prompt：system_prompt 留空＝用當前正式版。"""

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
    system_prompt, _version, _kind = prompt_debug_versions.resolve(
        body.system_prompt, allow_draft=True
    )

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
    """拿案例庫回歸驗證候選 Prompt；system_prompt 留空＝用當前正式版。"""

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
    system_prompt, _version, _kind = prompt_debug_versions.resolve(
        body.system_prompt, allow_draft=True
    )

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


# ── 批量跑批（上傳檔 × 當前正式版 Prompt → 斷點續跑批次）─────────────────────────────


class PromptDebugBatchResumeIn(BaseModel):
    """續跑/重跑請求：workers 缺省＝自動解析；rerun=true 忽略斷點全部重打。"""

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
    system_prompt: str = Form("", description="空＝取當前正式版"),
    sheet: str = Form("", description="XLSX 工作表名；空＝第一個工作表"),
    id_column: str = Form("session_oid"),
    text_column: str = Form("conversation_full"),
    offset: int = Form(0, ge=0),
    limit: int = Form(0, ge=0, description="實際跑多少條；0＝全部"),
    workers: int = Form(
        0,
        ge=0,
        description="併發 ceiling 覆寫；0＝自動（依 model 查表 + AIMD 自適應）。前端不再送此欄。",
    ),
    overrides: str = Form("", description="LlmOverridesIn 形狀的 JSON 字串；空＝沿用功能區默認"),
    config_name: str = Form(
        "", description="本次用的具名模型配置名（存進 manifest 供事後追溯）；空＝腳本直呼"
    ),
    user: dict = Depends(require_permission(permission_keys.PREJUDGE_RUN)),
) -> dict:
    """啟動批量跑批：存輸入檔 + 當前 Prompt 進 run 目錄，背景整批裁決；回初始進度快照（含 run_id）。

    單一配置入口，維持獨立於 `/batch/start-multi`——多配置只是多呼叫幾次
    `prompt_debug_batch.create_and_start`，這條路徑本身零分支。
    """
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
            config_name=config_name.strip(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _parse_config_entries_form(configs: str) -> list[dict]:
    """multipart 無巢狀結構，多模型跑批的配置清單以 JSON 陣列字串傳遞。

    每筆＝前端把使用者選中的具名模型配置攤平後的結果：
    `{config_name, provider, model, thinking?, reasoning_effort?, temperature?}`。

    **為什麼是 list 而不是「以 model 名為 key 的 dict」**：兩筆配置完全可能用同一個 model 只差
    旋鈕（`gpt-5.4-mini · medium` vs `gpt-5.4-mini · high` 正是具名配置最典型的用途），
    以 model 當 key 會讓後一筆靜默覆蓋前一筆——使用者選了 2 筆、只跑了 1 筆，而且不會知道。
    改以配置名去重：名稱在配置庫是全域唯一的（見 `settings._validate_model_configs`），天然是安全的
    識別鍵，且對使用者有意義（結果分欄直接標配置名）。

    Returns:
        `[{config_name, overrides}]`——`overrides` 是該配置的 flat 旋鈕（含 provider/model，
        不含 token），可直接餵 `effective_llm_dict` 與寫進 manifest。順序保留使用者的選取序。

    Raises:
        HTTPException: 400，不是合法 JSON 物件陣列、為空、缺 config_name/provider/model、
            provider 未登記、或配置名重複。
    """
    from app.core import settings as app_settings

    try:
        parsed = json.loads(configs)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"configs 參數不是合法 JSON：{exc}") from exc
    if not isinstance(parsed, list) or not all(isinstance(c, dict) for c in parsed):
        raise HTTPException(status_code=400, detail="configs 須為物件陣列")

    known_providers = {str(p.get("id")) for p in app_settings.LLM_PROVIDERS if p.get("id")}
    out: list[dict] = []
    seen_names: set[str] = set()
    for cfg in parsed:
        name = str(cfg.get("config_name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="每筆配置都需要 config_name")
        if name.casefold() in seen_names:
            raise HTTPException(status_code=400, detail=f"配置名重複：{name}")
        seen_names.add(name.casefold())

        provider = str(cfg.get("provider") or "").strip()
        if provider not in known_providers:
            raise HTTPException(
                status_code=400,
                detail=f"配置「{name}」的供應商 {provider!r} 未登記（可用：{sorted(known_providers)}）",
            )
        model = str(cfg.get("model") or "").strip()
        if not model:
            raise HTTPException(status_code=400, detail=f"配置「{name}」未指定 model")

        out.append(
            {
                "config_name": name,
                "overrides": {
                    "provider": provider,
                    "model": model,
                    "thinking": cfg.get("thinking") or "default",
                    "reasoning_effort": cfg.get("reasoning_effort") or "default",
                    # temperature 有「顯式 null＝用 API 預設」語意，key 一律帶上（見 effective_llm_dict）
                    "temperature": cfg.get("temperature"),
                },
            }
        )
    if not out:
        raise HTTPException(status_code=400, detail="至少需選擇一個模型配置")
    return out


@router.post("/prompt-debug/batch/start-multi")
async def prompt_debug_batch_start_multi(
    file: UploadFile | None = File(
        None, description="輸入 .csv/.xlsx/.xlsm（含 id 與對話欄）；改用 DB 取數時留空"
    ),
    source: str = Form(
        "", description="反饋來源 id（DB 取數模式必填，如 conversations）；上傳檔案時忽略"
    ),
    item_ids: str = Form(
        "", description="DB 取數模式的自然鍵清單（換行／逗號分隔，如 session_oid）"
    ),
    system_prompt: str = Form("", description="空＝取當前正式版"),
    sheet: str = Form("", description="XLSX 工作表名；空＝第一個工作表"),
    id_column: str = Form("session_oid"),
    text_column: str = Form("conversation_full"),
    offset: int = Form(0, ge=0),
    limit: int = Form(0, ge=0, description="實際跑多少條；0＝全部"),
    workers: int = Form(
        0,
        ge=0,
        description="併發 ceiling 覆寫；0＝自動（依 model 查表 + AIMD 自適應）。前端不再送此欄。",
    ),
    configs: str = Form(
        ...,
        description=(
            "JSON 物件陣列，每筆＝一個具名模型配置攤平後的旋鈕："
            '[{"config_name":"跑批·省錢","provider":"openai","model":"gpt-5.4-mini",'
            '"thinking":"default","reasoning_effort":"medium","temperature":null}]'
        ),
    ),
    user: dict = Depends(require_permission(permission_keys.PREJUDGE_RUN)),
) -> dict:
    """多模型並行跑批：同一份輸入 × 同一份 Prompt，逐一在每個**模型配置**上各起一個獨立 run。

    比較的單位是「配置」不是「model」——每筆自帶完整 provider + 旋鈕，因此可以並排比
    `seed-lite · high` vs `gpt-mini · medium` 這種連 effort 都不同的組合，也可以比同一個 model
    的兩種 effort。（舊契約是「多個 model 共用一組旋鈕」，這兩件事都做不到。）

    **不需要 `provider_id_for_model()` 那道硬驗證**：provider 是配置的顯式屬性，不用從 model 名猜。
    附帶鬆綁——自訂／未登記於 `llm_model.json` 的 model 名，第一次能合法用在多模型跑批
    （單模型路徑本來就可以）。缺 token 這類問題仍由 `_effective_or_400` 在啟動前逐筆攔下。

    各配置的實際啟動（`create_and_start_group`）彼此獨立：某筆因供應商未配 token 這類**執行期**
    問題啟動失敗，不影響其餘配置已經開始跑。
    """
    from app.judge import prompt_debug_batch

    # 逐筆解 effective（缺 token 在這裡就被攔下，不會先燒了前幾筆的預算才發現）
    entries = [
        {**e, "effective": _effective_or_400(e["overrides"])}
        for e in _parse_config_entries_form(configs)
    ]

    db_stats: dict | None = None
    if file is not None and file.filename:
        input_name = file.filename
        input_bytes = await file.read()
        if len(input_bytes) > _MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=400, detail="輸入檔超過 64MB 上限")
        if not input_bytes:
            raise HTTPException(status_code=400, detail="輸入檔是空的")
        resolved_id_col = id_column.strip() or "session_oid"
        resolved_text_col = text_column.strip() or "conversation_full"
    else:
        # DB 取數：撈完當場落成 CSV 快照，之後的路徑與上傳完全同構（見 build_db_input_csv 說明）
        ids = [chunk for raw in item_ids.splitlines() for chunk in raw.replace(",", " ").split()]
        try:
            resolved_id_col, resolved_text_col, input_bytes, db_stats = (
                prompt_debug_batch.build_db_input_csv(source.strip(), ids)
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        input_name = f"{source.strip()}-{db_stats['valid_rows']}筆.csv"

    try:
        result = prompt_debug_batch.create_and_start_group(
            input_name=input_name,
            input_bytes=input_bytes,
            sheet=sheet,
            id_column=resolved_id_col,
            text_column=resolved_text_col,
            offset=offset,
            limit=limit,
            workers=workers,
            system_prompt=system_prompt,
            entries=entries,
            triggered_by=user.get("email", ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # 取數統計回給前端：「要了 N 筆、查無 M 筆」必須講清楚，否則使用者只會看到總數對不上
    return {**result, "db_stats": db_stats} if db_stats else result


@router.get("/prompt-debug/batch/runs")
def prompt_debug_batch_runs(user: dict = Depends(auth.get_current_user)) -> dict:
    """全部跑批 run 摘要（新→舊；磁碟目錄為準、in-mem 快照 overlay 即時進度）。"""
    from app.judge import prompt_debug_batch

    return {"runs": prompt_debug_batch.list_runs()}


@router.get("/prompt-debug/batch/groups/{group_id}")
def prompt_debug_batch_group_status(
    group_id: str, user: dict = Depends(auth.get_current_user)
) -> dict:
    """單一多模型群組內所有 member run 的進度（供前端輪詢時按 model 分欄呈現）。"""
    from app.judge import prompt_debug_batch

    return {"group_id": group_id, "runs": prompt_debug_batch.list_runs(group_id=group_id)}


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
