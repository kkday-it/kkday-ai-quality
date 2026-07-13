"""初判歸因端點：前端發起批量判決 + 進度輪詢。

契約逐欄對齊 frontend/apps/console/src/api/judgment.api.ts（startPrejudge / getPrejudgeStatus）。
判決本體在 app/judge/prejudge_batch（背景 ThreadPool），本層只負責標的解析 + 設定注入 + job 轉發。
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core import auth, db
from app.core import settings as app_settings
from app.core.config import env, is_production
from app.core.permissions import permission_keys, require_permission
from app.judge import prejudge_batch

router = APIRouter(prefix="/judgment", tags=["judgment"])


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
            "拒絕以 stub 啟發式模式執行批量判決（假判決會覆蓋真實歸因）。"
            "請至設定面板配置 provider token，或設定環境變數 OPENAI_API_KEY。",
        )


class PrejudgeIn(BaseModel):
    """初判歸因請求：item_ids 顯式選取優先；否則 scope=all 依 stages 目標選取（可 within_ids 交集勾選範圍）。"""

    item_ids: list[str] | None = None
    source: str | None = None
    scope: str | None = None  # "all"＝依 stages 目標選取（item_ids 未給時生效）
    llm_config_id: str | None = None  # 指定已存 LLM 配置（缺＝active）
    voter_config_ids: list[str] | None = (
        None  # 跨廠 ensemble voter 模型集（低信心才複判投票；空/缺＝不 ensemble）
    )
    ensemble_sample_rate: float | None = (
        None  # ④抽樣稽核：高信心筆按此比例也跑 ensemble（0/缺＝純 confidence-gate）
    )
    product_verticals: list[str] | None = None  # 全局商品垂直分類（scope=all 時約束標的集合）
    # 目標選取（scope=all；stage 驅動）：預設只收未判；加選已判階段時可再收斂傾向/信心
    stages: list[str] | None = None  # 預設 ["unjudged"]
    target_polarity: list[str] | None = None  # 已判分支傾向收斂（多選 IN；如 ["negative"]）
    max_confidence: float | None = None  # 已判分支信心上限（confidence < 此值才收）
    within_ids: list[str] | None = None  # 範圍收斂：僅在此特徵 id 清單（勾選列）內做目標選取
    # 列表全維度篩選（scope=all；語義同 /api/problems，SSOT=_shared.apply_table_filters）：
    # 表級（兩分支皆套）＋ 判決級收斂（僅已判分支）——「歸因目標＝列表當前篩得到的東西」
    date_from: str | None = None  # 日期區間起（'YYYY-MM-DD'，含；表級）
    date_to: str | None = None  # 日期區間迄（含；表級）
    rec_oid: str | None = None  # 評論/特徵 id 精確篩選（表級）
    prod_oid: str | None = None  # 商品 OID（表級）
    order_oid: str | None = None  # 訂單 OID（表級）
    confidence_tier: str | None = None  # 信心分層收斂（已判分支；auto_accept/jury/needs_review）
    taxonomy: list[str] | None = None  # 歸因分類收斂（已判分支；任意層級 code 多選，子樹語義）
    has_external: bool | None = None  # 有無外部評論融合資料（表級，兩分支皆套；僅 product_reviews）


def _resolve_target_ids(body: PrejudgeIn) -> list[str]:
    """解析批量判決標的特徵 id 清單（start 與 count 預覽共用同一套，預覽即實跑）。

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


class PromptEvalIn(BaseModel):
    """單支 Prompt 測試（Prompt-as-Source 調適閉環）：prompt（polarity/C-1..C-6）+ N 樣本 + 可選 LLM 配置。

    filters 給定時（B1：按目前歸因列表篩選 × 單一 prompt 測試）：與 `PrejudgeIn` 同形，走
    `_resolve_target_ids` 解析出目標 id 集合，樣本改為該子集（取代 md5 全表抽樣）；未給沿用
    現行 production 參照抽樣（預設行為不變）。

    source="mock"（B3）：樣本改讀 `prompt_testcases` 邊界測試集（忽略 n/filters）。
    """

    prompt: str
    n: int = 8
    llm_config_id: str | None = None
    filters: PrejudgeIn | None = None
    source: str = "production"  # production | mock


@router.post("/prompt-eval")
async def prompt_eval(
    body: PromptEvalIn,
    user: dict = Depends(require_permission(permission_keys.JUDGMENT_PREJUDGE_RUN)),
) -> dict:
    """單支 Prompt 快測：抽 N 則現行判決為參照、只跑該支 prompt → 指標（primary/命中/棄權/多報）+ 分歧清單。

    同步評測以 asyncio.to_thread 卸到執行緒（不阻塞 event loop 單 worker）；set_current 的 contextvar 隨
    to_thread 的 copy_context 複製，故執行緒內 client 讀得到 effective LLM 設定。N 限 1~30（UI 快測；
    大樣本 / --source golden|mock / --compare 用 CLI scripts/tools/eval_prompt_single.py）。

    `body.filters` 給定時（B1）：樣本＝當前歸因列表篩選子集（與 `/prejudge/count` 同一套目標解析），
    忠實反映使用者在列表上選的範圍，而非全表 md5 抽樣。`body.source="mock"`（B3）：樣本改讀邊界
    測試集（忽略 n/filters）。
    """
    from app.judge import prompt_eval as pe
    from app.judge.llm import client

    if body.source not in ("production", "mock"):
        raise HTTPException(
            status_code=400, detail=f"未知 source：{body.source}（須為 production/mock）"
        )
    n = max(1, min(int(body.n or 8), 30))
    eff = app_settings.effective_llm_dict(
        app_settings.load_settings(user.get("user_id", "")), config_id=body.llm_config_id
    )
    _guard_not_stub_in_production(eff)
    app_settings.set_current(eff)
    client.set_llm_cache_read(False)  # 量測真實行為（寫入照常回填）
    client.set_usage_context({"job_id": f"prompt_eval_{body.prompt}"})
    is_mock = body.source == "mock"
    filter_ids = None if is_mock else (_resolve_target_ids(body.filters) if body.filters else None)
    try:
        result = await asyncio.to_thread(
            pe.run_eval, body.prompt, n, filter_ids=filter_ids, source=body.source
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    result["model"] = eff.get("model", "")

    # B2：測試歷史（每次完成落一列結果快照；單條 classify-one 是即時預覽，不落——見 prompt_eval.py 模組頂）。
    rule_code = f"prompt_{body.prompt}"
    prompt_version = next(
        (m["version"] for m in db.list_rule_meta() if m["rule_code"] == rule_code), None
    )
    history_source = "mock" if is_mock else ("filtered" if filter_ids is not None else "production")
    db.insert_prompt_eval_run(
        {
            "prompt_id": body.prompt,
            "prompt_version": prompt_version,
            "source": history_source,
            "n": result.get("n", n),
            "filters": (
                body.filters.model_dump(exclude_none=True) if body.filters and not is_mock else None
            ),
            "metrics": {k: v for k, v in result.items() if k != "mismatches"},
            "mismatches": result.get("mismatches", []),
            "model": result["model"],
            "triggered_by": user.get("email") or user.get("user_id", ""),
        }
    )
    return result


@router.get("/prompt-eval/runs")
def list_prompt_eval_runs(
    prompt_id: str,
    limit: int = 20,
    offset: int = 0,
    user: dict = Depends(auth.get_current_user),
) -> dict:
    """某支 prompt 的測試歷史列表（B2；created_at 降冪分頁）→ {total, items}——供「改 prompt 前後對比」。

    items 不含 mismatches（逐案分歧體積可觀），只列指標摘要；詳情走 `/prompt-eval/runs/{run_id}`。
    """
    return db.list_prompt_eval_runs(prompt_id, limit=limit, offset=offset)


@router.get("/prompt-eval/runs/{run_id}")
def get_prompt_eval_run(run_id: str, user: dict = Depends(auth.get_current_user)) -> dict:
    """單一測試 run 完整詳情（含 filters/mismatches 逐案分歧）。"""
    row = db.prompt_eval_run_detail(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="找不到此測試紀錄")
    return row


class PromptTestcaseIn(BaseModel):
    """單筆邊界測試 case（B3：手動新增 / 分歧一鍵入集共用入口）。"""

    text: str
    gold_l1: str  # 域機器值（對 domains.json 驗）
    gold_l2: str | None = None  # L2 面向 code（可空＝僅標「屬此域」；對該域 facets 驗）
    expected_polarity: str | None = None  # negative/neutral/positive（可空）
    note: str | None = None
    tags: list[str] | None = None


@router.post("/prompt-testcases")
def create_prompt_testcase(
    body: PromptTestcaseIn,
    user: dict = Depends(require_permission(permission_keys.JUDGMENT_PREJUDGE_RUN)),
) -> dict:
    """新增單筆邊界測試 case（手動新增 / 分歧一鍵入集共用）→ {id}。"""
    from app.judge import prompt_testcases as pt

    try:
        row = pt.validate_row(body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    row["created_by"] = user.get("email") or user.get("user_id", "")
    return {"id": db.insert_prompt_testcase(row)}


@router.post("/prompt-testcases/upload")
async def upload_prompt_testcases(
    file: UploadFile = File(...),
    user: dict = Depends(require_permission(permission_keys.JUDGMENT_PREJUDGE_RUN)),
) -> dict:
    """CSV 批量上傳邊界測試 case（獨立輕量 parse，不走 inbound 管線）→ {inserted, skipped, errors}。

    欄位：text,gold_l1,gold_l2,expected_polarity,note,tags；gold_l1/gold_l2/expected_polarity 逐行
    驗證（對 domains.json + 該域 facets + 三態），不合法者不入庫、回行號 + 原因；重複 text（含既有
    資料）跳過並計入 skipped。
    """
    from app.judge import prompt_testcases as pt

    content = await file.read()
    valid, errors = pt.parse_csv(content)
    by = user.get("email") or user.get("user_id", "")
    for row in valid:
        row["created_by"] = by
    result = db.bulk_insert_prompt_testcases(valid) if valid else {"inserted": 0, "skipped": 0}
    result["errors"] = errors
    return result


@router.get("/prompt-testcases")
def list_prompt_testcases(
    gold_l1: str | None = None,
    tags: str | None = None,  # 逗號分隔
    enabled: bool | None = None,
    limit: int = 50,
    offset: int = 0,
    _: dict = Depends(auth.get_current_user),
) -> dict:
    """邊界測試集列表（篩 gold_l1/tags/enabled，分頁）→ {total, items}。"""
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
    return db.list_prompt_testcases(
        gold_l1=gold_l1, tags=tag_list, enabled=enabled, limit=limit, offset=offset
    )


class PromptTestcasePatch(BaseModel):
    """局部更新邊界測試 case（enabled 開關 / 修正 gold 值等；缺省欄位不動）。"""

    text: str | None = None
    gold_l1: str | None = None
    gold_l2: str | None = None
    expected_polarity: str | None = None
    note: str | None = None
    tags: list[str] | None = None
    enabled: bool | None = None


@router.patch("/prompt-testcases/{tc_id}")
def update_prompt_testcase(
    tc_id: str,
    body: PromptTestcasePatch,
    user: dict = Depends(require_permission(permission_keys.JUDGMENT_PREJUDGE_RUN)),
) -> dict:
    """局部更新一筆測試 case；改動 text/gold_l1/gold_l2/expected_polarity 時與既有值合併後重新驗證
    （gold_l2 是否屬於 gold_l1 需以合併後的完整狀態判斷）。
    """
    from app.judge import prompt_testcases as pt

    patch = body.model_dump(exclude_none=True)
    if not patch:
        raise HTTPException(status_code=400, detail="無更新欄位")
    if {"text", "gold_l1", "gold_l2", "expected_polarity"} & patch.keys():
        current = db.get_prompt_testcase(tc_id)
        if current is None:
            raise HTTPException(status_code=404, detail="找不到此測試 case")
        try:
            validated = pt.validate_row({**current, **patch})
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from None
        patch.update({k: v for k, v in validated.items() if k in patch})
    if not db.update_prompt_testcase(tc_id, patch):
        raise HTTPException(status_code=404, detail="找不到此測試 case")
    return {"updated": True}


@router.delete("/prompt-testcases/{tc_id}")
def delete_prompt_testcase(
    tc_id: str, user: dict = Depends(require_permission(permission_keys.JUDGMENT_PREJUDGE_RUN))
) -> dict:
    """刪除單筆測試 case。"""
    if not db.delete_prompt_testcase(tc_id):
        raise HTTPException(status_code=404, detail="找不到此測試 case")
    return {"deleted": True}


class ClassifyOneIn(BaseModel):
    """單條評論 dry-run 分類（歸因列表「測試」）：來源 + 業務 id。"""

    source: str
    source_id: str
    llm_config_id: str | None = None


@router.post("/classify-one")
async def classify_one(
    body: ClassifyOneIn,
    user: dict = Depends(require_permission(permission_keys.JUDGMENT_PREJUDGE_RUN)),
) -> dict:
    """單條評論 dry-run 分類:跑 prompts 判這一則 → 結果,**不落庫**（預覽「改 prompt 後怎麼判」）。

    比照 /prompt-eval:effective LLM + guard not stub + asyncio.to_thread（不阻塞單 worker,contextvar
    隨 copy_context 傳遞）。與列級「初判分類」（重判並覆寫落庫）區隔——本端點只讀不寫。
    """
    from app.judge import prompt_eval as pe
    from app.judge.llm import client

    eff = app_settings.effective_llm_dict(
        app_settings.load_settings(user.get("user_id", "")), config_id=body.llm_config_id
    )
    _guard_not_stub_in_production(eff)
    app_settings.set_current(eff)
    client.set_llm_cache_read(False)  # 量測真實行為
    client.set_usage_context({"job_id": f"classify_one_{body.source_id}"})
    try:
        return await asyncio.to_thread(pe.classify_one, body.source, body.source_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


@router.post("/prejudge/count")
def prejudge_count(body: PrejudgeIn, _: dict = Depends(auth.get_current_user)) -> dict:
    """預覽批量判決「將處理 N 筆」→ {total}（與 start 同一套標的解析；不派工、不消耗 token）。"""
    return {"total": len(_resolve_target_ids(body))}


@router.post("/prejudge")
def start_prejudge(
    body: PrejudgeIn,
    user: dict = Depends(require_permission(permission_keys.JUDGMENT_PREJUDGE_RUN)),
) -> dict:
    """啟動初判歸因批量任務 → {job_id, total, model}（立即回，背景派工）。

    標的解析：_resolve_target_ids（item_ids 顯式 > scope=all 目標選取，可 within_ids 交集勾選範圍）。
    設定注入：以當前 user 的 effective LLM dict（可選 llm_config_id）供 judge 路徑跨 thread 讀取。
    """
    uid = user.get("user_id", "")
    s = app_settings.load_settings(uid)
    eff = app_settings.effective_llm_dict(s, config_id=body.llm_config_id)
    _guard_not_stub_in_production(eff)
    model = eff.get("model", "")
    # 跨廠 ensemble voter：勾選的其他 config 各組整套 effective dict（排除與主判決同一 config；空→None＝不 ensemble）
    voter_cfgs = [
        app_settings.effective_llm_dict(s, config_id=cid)
        for cid in (body.voter_config_ids or [])
        if cid and cid != body.llm_config_id
    ] or None

    item_ids = _resolve_target_ids(body)

    # 歸因歷史語境：觸發型態（batch=目標選取/selected=顯式多筆或勾選範圍/single=單筆）+ 是否重判 + 參數快照
    if body.item_ids:
        kind = "single" if len(body.item_ids) == 1 else "selected"
        rejudge = db.any_judged(body.source, item_ids)  # 顯式選取：標的已有判決＝重判
    else:
        kind = (
            "selected" if body.within_ids else "batch"
        )  # 勾選範圍內目標選取：歷史語境仍屬顯式選取
        rejudge = any(
            s != "unjudged" for s in (body.stages or ["unjudged"])
        )  # 目標選取：收已判階段＝重判
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
        voter_cfgs=voter_cfgs,
        sample_rate=body.ensemble_sample_rate or 0.0,
        triggered_by=user.get("email") or uid,
        kind=kind,
        rejudge=rejudge,
        params=params,
        # exact-cache 讀取閘：批次（scope 目標選取）重用規則未變部分；顯式單筆/選取重判＝使用者要求真的重打
        cache_read=(kind == "batch"),
    )
    return {
        "job_id": job_id,
        "total": len(item_ids),
        "model": model,
        "ensemble_voters": len(voter_cfgs or []),
    }


@router.post("/prejudge/pause")
def pause_prejudge(
    job_id: str, _: dict = Depends(require_permission(permission_keys.JUDGMENT_PREJUDGE_RUN))
) -> dict:
    """暫停執行中的初判歸因任務（提交迴圈阻塞、已在跑的收斂後 processed 停增）→ 回更新後快照。"""
    if not prejudge_batch.pause_job(job_id):
        raise HTTPException(status_code=409, detail=f"job 不存在或非執行中，無法暫停：{job_id}")
    return prejudge_batch.get_job(job_id) or {}


@router.post("/prejudge/resume")
def resume_prejudge(
    job_id: str, _: dict = Depends(require_permission(permission_keys.JUDGMENT_PREJUDGE_RUN))
) -> dict:
    """恢復已暫停的初判歸因任務（提交迴圈續跑）→ 回更新後快照。"""
    if not prejudge_batch.resume_job(job_id):
        raise HTTPException(status_code=409, detail=f"job 不存在或非暫停中，無法恢復：{job_id}")
    return prejudge_batch.get_job(job_id) or {}


@router.post("/prejudge/cancel")
def cancel_prejudge(
    job_id: str, _: dict = Depends(require_permission(permission_keys.JUDGMENT_PREJUDGE_RUN))
) -> dict:
    """停止初判歸因任務（不再派新工，已在跑的收斂後轉 cancelled）→ 回更新後快照。

    已判 finding 已即時落庫保留；欲繼續可對「剩餘未判」重新發起（scope=all）。
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
    """歸因歷史列表（每次批量/選取/單筆重判一列；started_at 降冪分頁）→ {total, items}。

    執行中 run 疊加 in-mem 即時進度（_overlay_live）；token/費用終態值來自 usage sink 累計。
    """
    data = db.list_judgment_runs(limit=limit, offset=offset, source=source)
    data["items"] = [_overlay_live(r) for r in data["items"]]
    return data


@router.get("/runs/{job_id}")
def run_detail(job_id: str, _: dict = Depends(auth.get_current_user)) -> dict:
    """單一 run 詳情：run 欄位 + 發起參數快照 + llm_usage per-stage 明細（呼叫數/token/費用）。

    per-stage 明細於 job 結束 flush llm_usage 後才有值（執行中為空陣列）。
    """
    run = db.judgment_run_detail(job_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"歸因歷史不存在：{job_id}")
    return _overlay_live(run)


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
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
