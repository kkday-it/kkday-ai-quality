"""初判歸因端點：前端發起批量初判 + 進度輪詢。

契約逐欄對齊 frontend/apps/console/src/api/judgment.api.ts（startPrejudge / getPrejudgeStatus）。
初判本體在 app/judge/prejudge_batch（背景 ThreadPool），本層只負責標的解析 + 設定注入 + job 轉發。

`LlmOverridesIn`/`PrejudgeIn`/`_resolve_target_ids` 為三領域（prejudge/prompt_debug/prompt_sandbox）
共用契約與標的解析邏輯，由 prompt_sandbox.py 直接 import 重用（同 prompt_eval 重用
prejudge._gate_attrs 的既有慣例，不另立第三個共用模組）；prompt_debug.py 只需 LlmOverridesIn。
"""

from __future__ import annotations

import asyncio
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core import auth, db
from app.core import settings as app_settings
from app.core.config import env, is_production
from app.core.permissions import permission_keys, require_permission
from app.judge import prejudge_batch, run_log

router = APIRouter(prefix="/prejudge", tags=["prejudge"])

# SSE 輪詢間隔：/stream 推初判進度、/log-stream 推逐筆執行日誌，皆非跨環境變化值，具名純為避免裸數字。
_PREJUDGE_SSE_POLL_INTERVAL = 0.8
_LOG_SSE_POLL_INTERVAL = 0.4


def _guard_not_stub_in_production(eff: dict) -> None:
    """正式環境 stub 硬閘：解不出真 token（將全程走 stub 啟發式假判）→ 403 拒啟動批量。

    dev 保留零 key 跑通閉環的既有行為；正式環境假判會靜默覆蓋真實歸因
    （曾致 1,452 筆假判事故，靠 pg_dump 還原），故在入口即擋、給明確錯誤。

    Raises:
        HTTPException: 403，正式環境且 effective 設定解不出任何 LLM token。
    """
    if is_production() and not app_settings.resolve_provider_token(eff):
        raise HTTPException(
            status_code=403,
            detail=f"目前環境（APP_ENV={env.app_env}）偵測不到有效 LLM token，"
            "拒絕以 stub 啟發式模式執行批量初判（假初判會覆蓋真實歸因）。"
            "請至設定面板配置 provider token，或設定環境變數 OPENAI_API_KEY。",
        )


class LlmOverridesIn(BaseModel):
    """本次執行臨時旋鈕覆寫（不落庫；「存為此功能區默認」為前端另一顯式動作，走 /api/settings）。

    三功能區（prejudge/prompt_debug/sandbox）共用同一契約——provider 可切換本次用哪個供應商連線，
    其餘旋鈕覆寫該區默認。缺省欄位＝沿用功能區默認，前端 LlmConfigPicker/LlmKnobs 對齊此形狀。
    """

    provider: str | None = None
    model: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    thinking: Literal["default", "on", "off"] | None = None
    reasoning_effort: (
        Literal["default", "none", "minimal", "low", "medium", "high", "xhigh"] | None
    ) = None


class PrejudgeIn(BaseModel):
    """初判歸因請求：item_ids 顯式選取優先；否則 scope=all 依 stages 目標選取（可 within_ids 交集勾選範圍）。"""

    item_ids: list[str] | None = None
    source: str | None = None
    scope: str | None = None  # "all"＝依 stages 目標選取（item_ids 未給時生效）
    overrides: LlmOverridesIn | None = None  # 本次臨時旋鈕覆寫；缺省沿用 prejudge 功能區默認
    product_verticals: list[str] | None = None  # 全局商品垂直分類（scope=all 時約束標的集合）
    # 目標選取（scope=all；stage 驅動）：預設只收未初判；加選已初判階段時可再收斂傾向/信心
    stages: list[str] | None = None  # 預設 ["unjudged"]
    target_polarity: list[str] | None = None  # 已初判分支傾向收斂（多選 IN；如 ["negative"]）
    max_confidence: float | None = None  # 已初判分支信心上限（confidence < 此值才收）
    within_ids: list[str] | None = None  # 範圍收斂：僅在此特徵 id 清單（勾選列）內做目標選取
    # 列表全維度篩選（scope=all；語義同 /api/problems，SSOT=_shared.apply_table_filters）：
    # 表級（兩分支皆套）＋ 初判級收斂（僅已初判分支）——「歸因目標＝列表當前篩得到的東西」
    date_from: str | None = None  # 日期區間起（'YYYY-MM-DD'，含；表級）
    date_to: str | None = None  # 日期區間迄（含；表級）
    rec_oid: str | None = None  # 評論/特徵 id 精確篩選（表級）
    prod_oid: str | None = None  # 商品 OID（表級）
    order_oid: str | None = None  # 訂單 OID（表級）
    confidence_tier: str | None = None  # 信心分層收斂（已初判分支；auto_accept/jury/needs_review）
    taxonomy: list[str] | None = None  # 歸因分類收斂（已初判分支；任意層級 code 多選，子樹語義）
    has_external: bool | None = None  # 有無外部評論融合資料（表級，兩分支皆套；僅 product_reviews）
    # 版本選擇功能：正式初判可指定 7 條 prompt 各自要用哪個歷史版本（預設沿用 active）。
    prompt_versions: dict[str, int] | None = None  # {rule_code: 版本號}


def _resolve_target_ids(body: PrejudgeIn) -> list[str]:
    """解析批量初判標的特徵 id 清單（start 與 count 預覽共用同一套，預覽即實跑）。

    item_ids 顯式 > scope=="all" 走 stage 驅動目標選取（可 within_ids 交集勾選範圍）> 空集合。
    """
    if body.item_ids:
        return body.item_ids
    if body.scope == "all":
        return db.prejudge_target_ids(
            body.source,
            body.product_verticals,
            stages=body.stages,
            target_polarity=body.target_polarity,
            max_confidence=body.max_confidence,
            date_from=body.date_from,
            date_to=body.date_to,
            rec_oid=body.rec_oid,
            prod_oid=body.prod_oid,
            order_oid=body.order_oid,
            confidence_tier=body.confidence_tier,
            taxonomy=body.taxonomy,
            has_external=body.has_external,
            within_ids=body.within_ids,
        )
    return []


@router.post("/count")
def prejudge_count(body: PrejudgeIn, _: dict = Depends(auth.get_current_user)) -> dict:
    """預覽批量初判「將處理 N 筆」→ {total}（與 start 同一套標的解析；不派工、不消耗 token）。"""
    return {"total": len(_resolve_target_ids(body))}


@router.post("")
def start_prejudge(
    body: PrejudgeIn,
    user: dict = Depends(require_permission(permission_keys.PREJUDGE_RUN)),
) -> dict:
    """啟動初判歸因批量任務 → {job_id, total, model}（立即回，背景派工）。

    標的解析：_resolve_target_ids（item_ids 顯式 > scope=all 目標選取，可 within_ids 交集勾選範圍）。
    設定注入：以 prejudge 功能區 effective LLM dict（可選 overrides）供 judge 路徑跨 thread 讀取。
    """
    s = app_settings.load_settings()
    overrides = body.overrides.model_dump(exclude_unset=True) if body.overrides else None
    eff = app_settings.effective_llm_dict(s, area="prejudge", overrides=overrides)
    _guard_not_stub_in_production(eff)
    model = eff.get("model", "")

    item_ids = _resolve_target_ids(body)

    # 歸因歷史語境：觸發型態（batch=目標選取/selected=顯式多筆或勾選範圍/single=單筆）+ 是否重新初判 + 參數快照
    if body.item_ids:
        kind = "single" if len(body.item_ids) == 1 else "selected"
        rejudge = db.any_judged(body.source, item_ids)  # 顯式選取：標的已有初判＝重新初判
    else:
        kind = (
            "selected" if body.within_ids else "batch"
        )  # 勾選範圍內目標選取：歷史語境仍屬顯式選取
        rejudge = any(
            s != "unjudged" for s in (body.stages or ["unjudged"])
        )  # 目標選取：收已初判階段＝重新初判
    params = body.model_dump(exclude_none=True)
    # id 大清單不進參數快照（防 params 膨脹）；≤20 筆保留供單筆/小批追溯
    for key in ("item_ids", "within_ids"):
        if len(params.get(key) or []) > 20:
            params[key] = params[key][:20]
    params["item_ids_count"] = len(item_ids)

    job_id = prejudge_batch.start_job(
        item_ids,
        eff,
        model,
        source=body.source,
        triggered_by=user.get("email", ""),
        kind=kind,
        rejudge=rejudge,
        params=params,
        # exact-cache 讀取閘：批次（scope 目標選取）重用規則未變部分；顯式單筆/選取重新初判＝使用者要求真的重打
        cache_read=(kind == "batch"),
        prompt_versions=body.prompt_versions,
    )
    return {
        "job_id": job_id,
        "total": len(item_ids),
        "model": model,
    }


@router.post("/pause")
def pause_prejudge(
    job_id: str, _: dict = Depends(require_permission(permission_keys.PREJUDGE_RUN))
) -> dict:
    """暫停執行中的初判歸因任務（提交迴圈阻塞、已在跑的收斂後 processed 停增）→ 回更新後快照。"""
    if not prejudge_batch.pause_job(job_id):
        raise HTTPException(status_code=409, detail=f"job 不存在或非執行中，無法暫停：{job_id}")
    return prejudge_batch.get_job(job_id) or {}


@router.post("/resume")
def resume_prejudge(
    job_id: str, _: dict = Depends(require_permission(permission_keys.PREJUDGE_RUN))
) -> dict:
    """恢復已暫停的初判歸因任務（提交迴圈續跑）→ 回更新後快照。"""
    if not prejudge_batch.resume_job(job_id):
        raise HTTPException(status_code=409, detail=f"job 不存在或非暫停中，無法恢復：{job_id}")
    return prejudge_batch.get_job(job_id) or {}


@router.post("/cancel")
def cancel_prejudge(
    job_id: str, _: dict = Depends(require_permission(permission_keys.PREJUDGE_RUN))
) -> dict:
    """停止初判歸因任務（不再派新工，已在跑的收斂後轉 cancelled）→ 回更新後快照。

    已初判 finding 已即時落庫保留；欲繼續可對「剩餘未初判」重新發起（scope=all）。
    """
    if not prejudge_batch.cancel_job(job_id):
        raise HTTPException(status_code=409, detail=f"job 不存在或已結束，無法停止：{job_id}")
    return prejudge_batch.get_job(job_id) or {}


def _overlay_live(run: dict) -> dict:
    """對非終態 run 疊加 in-mem job 快照（即時進度/花費）；快照已不在（server 重啟）→ 標 interrupted。

    歸因歷史 DB 只在暫停/恢復/終態回寫，執行中的 processed/token/費用活在 prejudge_batch 記憶體；
    列表/詳情讀取時 merge，前端拿到的即為當下實況。
    """
    if run.get("status") in ("running", "paused", "cancelling"):
        snap = prejudge_batch.get_job(run["job_id"])
        if snap:
            run.update(
                {
                    "status": snap["status"],
                    "processed": snap["processed"],
                    "ok": snap["ok"],
                    "failed": snap["failed"],
                    "total_tokens": snap["total_tokens"],
                    "cost_usd": snap["cost_usd"],
                }
            )
        else:
            run["status"] = "interrupted"  # server 重啟中斷：無快照可續，如實標記
    return run


@router.get("/runs")
def list_runs(
    limit: int = 20,
    offset: int = 0,
    source: str | None = None,
    _: dict = Depends(auth.get_current_user),
) -> dict:
    """歸因歷史列表（每次批量/選取/單筆重新初判一列；started_at 降冪分頁）→ {total, items}。

    執行中 run 疊加 in-mem 即時進度（_overlay_live）；token/費用終態值來自 usage sink 累計。
    """
    data = db.list_prejudge_runs(limit=limit, offset=offset, source=source)
    data["items"] = [_overlay_live(r) for r in data["items"]]
    return data


@router.get("/runs/{job_id}")
def run_detail(job_id: str, _: dict = Depends(auth.get_current_user)) -> dict:
    """單一 run 詳情：run 欄位 + 發起參數快照 + llm_usage per-stage 明細（呼叫數/token/費用）。

    per-stage 明細於 job 結束 flush llm_usage 後才有值（執行中為空陣列）。
    """
    run = db.prejudge_run_detail(job_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"歸因歷史不存在：{job_id}")
    return _overlay_live(run)


@router.get("/runs/{job_id}/log")
def run_log_detail(job_id: str, _: dict = Depends(auth.get_current_user)) -> dict:
    """讀某次初判落庫的完整執行日誌快照（歸因歷史「查看 LLM 日誌」入口）。

    僅小批量 job（run_log.LOG_JOB_MAX_ITEMS 內）有收集內容；大批量 / 啟用日誌前的舊初判回 404。
    """
    entries = db.get_run_log(job_id)
    if entries is None:
        raise HTTPException(status_code=404, detail=f"此任務無執行日誌快照：{job_id}")
    return {"entries": entries}


@router.get("/stream")
async def prejudge_stream(job_id: str) -> StreamingResponse:
    """SSE 長連線推送初判歸因進度（免前端輪詢）：每 ~0.8s 推一次快照，job done/error/cancelled 即關閉。

    不加 auth Depends：原生 EventSource 無法帶 Authorization header；job_id 為不可猜的隨機
    capability token（僅發起初判的登入者取得），以其本身作為存取憑證（與上傳 SSE 一致）。
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
            await asyncio.sleep(_PREJUDGE_SSE_POLL_INTERVAL)

    return StreamingResponse(
        _events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/log-stream")
async def prejudge_log_stream(job_id: str, offset: int = 0) -> StreamingResponse:
    """SSE 推送單次初判 job 的執行日誌（各階段 + LLM 輸入參數/prompt/輸出）——供前端抽屜即時檢視。

    僅小批量 job 收集日誌（run_log.LOG_JOB_MAX_ITEMS）；每筆 entry 一個 event 增量推送
    （offset 支援續讀），日誌收集結束且讀盡即推 done 關閉。不加 auth Depends：同 /prejudge/stream
    （原生 EventSource 帶不了 Authorization header，job_id 為不可猜的 capability token）。
    """

    async def _events():
        """增量 entry → SSE event 產生器；job 無日誌推 error、done 且讀盡推 done 後結束。

        job 剛建立時 `run_log.bind(job_id)` 發生在背景 thread（見 prejudge_batch._run），
        前端拿到 job_id 立刻連上時可能尚未 bind——給予短暫寬限重試，避免把「還沒 bind」
        誤判成「無日誌」立即關流（首次初判日誌永遠空白的競態根因）。
        """
        idx = max(0, offset)
        grace = 5.0  # 尚未 bind 的寬限秒數；超過才視為真的不收集日誌（如大批量 job）
        while True:
            batch, done, exists = run_log.read(job_id, idx)
            if not exists:
                if grace > 0:
                    grace -= _LOG_SSE_POLL_INTERVAL
                    await asyncio.sleep(_LOG_SSE_POLL_INTERVAL)
                    continue
                msg = json.dumps(
                    {"detail": "此任務無執行日誌（僅小批量任務收集）"}, ensure_ascii=False
                )
                yield f"event: error\ndata: {msg}\n\n"
                return
            for e in batch:
                yield f"data: {json.dumps(e, ensure_ascii=False)}\n\n"
            idx += len(batch)
            if done and not batch:
                yield "event: done\ndata: {}\n\n"
                return
            await asyncio.sleep(_LOG_SSE_POLL_INTERVAL)

    return StreamingResponse(
        _events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
