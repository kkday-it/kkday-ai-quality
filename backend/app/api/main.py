"""kkday-ai-quality 後端入口（FastAPI）。

AI 商品質檢平台 — AI 法官（內容爭議裁決，內容質量 Pod 第三支柱）。
本階段已實作：L1 資料錄入（CSV/Excel 批量 + 單個）→ 本地 SQLite。
判決層 L2–L4 待 OpenAI key（沿用 ProductContentAIChecker 資產）。

啟動：uvicorn app.api.main:app --reload --port 8100
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# config 先載入：import 時即讀 backend/.env 並建 env 單例（機密集中管理）。
from app.core import auth, config, db  # noqa: F401  (config import 觸發 .env 載入)
from app.core import source_mapping as srcmap
from app.judge.ingest import entry, upload_batch
from app.judge.llm import client as llm_client

app = FastAPI(title="kkday-ai-quality", version="0.0.1")

app.add_middleware(
    CORSMiddleware,
    # 部署換 domain / 加 staging 免改碼：env CORS_ALLOW_ORIGINS 逗號分隔（預設對齊 vite dev 5273）
    allow_origins=[o.strip() for o in config.env.cors_allow_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)

db.init_db()

# ── 新攝取架構（PostgreSQL）端點：掛載於 /api/v1 ──
# 舊 sqlite 端點（帳號 / 錄入 / 判決）留於下方，第一階段並存，第三階段汰換。
from app.api.routers import v1_router  # noqa: E402

app.include_router(v1_router)  # 啟動即建表（冪等）

# ── config/taxonomy JSON 線上查看 / 編輯端點（規則 tab）；prefix 自帶 /api/config ──
from app.api.routers import config as config_router  # noqa: E402

app.include_router(config_router.router)

# ── 判決規則管理（config/ai_judge/ 版本化）；prefix 自帶 /api/judge-rules ──
from app.api.routers import rules as rules_router  # noqa: E402

app.include_router(rules_router.router)
db.seed_rules_from_files()  # 初次播種：無 DB 版的 rule 以默認檔建 v1 active（冪等）


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# ── 帳號系統（註冊 / 登入 / 當前使用者）──────────────────────────────
class RegisterIn(BaseModel):
    email: str
    password: str


class LoginIn(BaseModel):
    email: str
    password: str


def _public_user(user: dict) -> dict:
    """去除 password_hash，只回傳可公開欄位。"""
    return {
        "user_id": user["user_id"],
        "email": user["email"],
        "created_at": user.get("created_at"),
    }


@app.post("/api/auth/register")
def register(body: RegisterIn) -> dict:
    """註冊新帳號 → 回 JWT + user。email 重複回 409。"""
    email = body.email.strip().lower()
    if "@" not in email or len(body.password) < config.env.min_password_length:
        raise HTTPException(
            status_code=400,
            detail=f"email 格式錯誤或密碼少於 {config.env.min_password_length} 碼",
        )
    try:
        user = db.create_user(str(uuid.uuid4()), email, auth.hash_password(body.password))
    except db.DuplicateEmailError:
        raise HTTPException(status_code=409, detail="此 email 已註冊") from None
    return {"token": auth.create_access_token(user["user_id"]), "user": _public_user(user)}


@app.post("/api/auth/login")
def login(body: LoginIn) -> dict:
    """登入 → 回 JWT + user。帳密錯誤回 401。"""
    email = body.email.strip().lower()
    user = db.get_user_by_email(email)
    if not user or not auth.verify_password(body.password, user["password_hash"] or ""):
        raise HTTPException(status_code=401, detail="email 或密碼錯誤")
    return {"token": auth.create_access_token(user["user_id"]), "user": _public_user(user)}


@app.get("/api/auth/me")
def me(user: dict = Depends(auth.get_current_user)) -> dict:
    """回傳當前登入使用者。"""
    return _public_user(user)


def load_user_context(user: dict = Depends(auth.get_current_user)) -> dict:
    """認證守衛（settings / judge 端點共用）。

    註：contextvar 須在 handler「同一 threadpool thread」內設定才對 judge 路徑可見；
    FastAPI sync dependency 與 sync endpoint 可能跑在不同 thread，故 set_current 改由
    各 handler 內呼叫 _activate_settings，不在此 Depends 設（跨 thread 不可見）。
    """
    return user


def _activate_settings(user_id: str) -> None:
    """在 handler 內注入該 user 設定到 contextvar（同 thread，judge 路徑 llm client 才讀得到）。"""
    from app.core import settings as app_settings

    s = app_settings.load_settings(user_id)
    # judge 路徑讀 contextvar：注入「active LLM config + provider_tokens」組出的 effective flat dict
    # （client._resolve 所讀 key 不變 → client.py 零改動）
    app_settings.set_current(app_settings.effective_llm_dict(s))


# ── L1 資料錄入（CSV/Excel 批量 + 單個）→ 本地 SQLite ──────────────────
class SingleEntryIn(BaseModel):
    prod_oid: str
    comment: str
    rating: int | None = None
    pkg_oid: str = ""


class UploadSelection(BaseModel):
    """確認匯入時用戶勾選的工作表：sheet_name + 確認來源（通常＝自動辨識結果）。"""

    sheet_name: str
    source: str


@app.post("/api/inbound/validate")
async def validate_inbound(file: UploadFile = File(...)) -> dict:
    """乾跑校驗（不落庫）：逐工作表自動辨識來源 + 必備表頭校驗，回每表能否上傳。

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
                    "sheet_name": sh["sheet_name"], "detected_source": None, "label": "",
                    "status": "unknown", "missing_headers": [], "row_count": len(sh["rows"]),
                    "reason": "表頭無法對應任何已知來源（非 5 反饋源，略過）",
                }
            )
            continue
        missing = srcmap.validate_headers(src, headers)
        report.append(
            {
                "sheet_name": sh["sheet_name"], "detected_source": src,
                "label": srcmap.source_label(src),
                "status": "ok" if not missing else "fail",
                "missing_headers": missing, "row_count": len(sh["rows"]),
                "reason": "" if not missing else f"缺必備欄：{'、'.join(missing)}",
            }
        )
    return {"filename": file.filename, "sheets": report}


@app.post("/api/inbound/upload")
async def upload_inbound(
    file: UploadFile = File(...),
    selections: str = Form(...),
) -> dict:
    """確認匯入（背景 job）：解析 + 校驗勾選工作表 → 註冊背景任務逐表分塊落庫 → 立即回 {job_id, sheets}。

    selections：JSON 字串 `[{"sheet_name","source"}]`（來自 /validate 後用戶勾選）。
    大檔（數萬列）改走背景 job + 前端輪詢 `/api/inbound/upload/status` 畫每表進度條；逐列容錯、
    壞列跳過並回報原因。product_reviews 走專表 ingestor，其餘來源沿用 intake_items 通用路徑。
    """
    content = await file.read()
    try:
        sel = [UploadSelection(**s).model_dump() for s in json.loads(selections)]
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=f"selections 格式錯誤：{e}") from None
    if not sel:
        raise HTTPException(status_code=400, detail="未選擇任何工作表")
    return upload_batch.start_upload_job(content, file.filename or "", sel)


@app.get("/api/inbound/upload/status")
def upload_inbound_status(job_id: str) -> dict:
    """上傳背景 job 進度快照（輪詢後備；主推 /stream SSE）：status + 每表 processed/total/inserted/failed。"""
    snap = upload_batch.get_job(job_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="job 不存在")
    return snap


@app.get("/api/inbound/upload/stream")
async def upload_inbound_stream(job_id: str) -> StreamingResponse:
    """SSE 長連線推送上傳進度（免前端輪詢）：伺服器讀 in-mem 快照，每 ~0.6s 推一次 event，job 結束即關閉。

    單向 server→client 進度推送用 SSE 最貼切（不需 WebSocket 雙向）；`X-Accel-Buffering: no`
    關閉 nginx 緩衝確保即時。前端以原生 EventSource 接收、status≠running 時關閉連線。
    """

    async def _events():
        """快照 → SSE event 產生器；job 不存在推 error、終態推完即 return 結束串流。"""
        while True:
            snap = upload_batch.get_job(job_id)
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
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@app.get("/api/batches")
def get_batches() -> list[dict]:
    """上傳批次清單（新到舊）。"""
    return db.list_batches()


@app.get("/api/batches/{batch_id}/items")
def get_batch_items(batch_id: str) -> list[dict]:
    """某批次錄入明細（5 來源拆表後源表不帶 batch_id，故不再逐批次列出，回空）。"""
    return []


# ── L2–L4 判決層已移除（2026-06-29 清理，待依新 config/taxonomy 重建）──
# 原 /api/diagnose · /api/diagnose/conversations 隨判決鏈刪除；
# 下方 findings 讀取端點（db-backed）保留，judgments 表為空直到判決層重建。


class LlmConfigIn(BaseModel):
    """單套 LLM config（機密 token 不在此，走共用 provider_tokens）。id 新建留空，後端補 uuid。"""

    id: str | None = None
    label: str = "未命名配置"
    provider: str | None = None
    base_url: str | None = None
    model: str | None = None
    temperature: float | None = None
    thinking: str | None = None
    reasoning_effort: str | None = None


class QcConfigIn(BaseModel):
    """單套 QC DB config。password 為 transient 欄位：後端抽出存 qc_passwords[id]，不落 config 本體。"""

    id: str | None = None
    label: str = "未命名連線"
    env: str | None = None
    host: str | None = None
    port: int | None = None
    user: str | None = None
    names: list[str] = []
    schemas: list[str] = []
    password: str | None = None  # transient；空/遮罩不覆蓋既有


class SettingsIn(BaseModel):
    """設定部分/整包 patch（皆選填，缺省欄位不動）。機密空/遮罩值後端不覆蓋既有。"""

    # LLM 多 config
    llm_configs: list[LlmConfigIn] | None = None
    active_llm_config_id: str | None = None
    provider_tokens: dict | None = None  # { provider_id: token } 跨 config 共用
    provider_models: dict | None = None  # 各供應商自訂 model 清單
    # QC DB 多 config
    qc_configs: list[QcConfigIn] | None = None
    active_qc_config_id: str | None = None
    # 規整
    taxonomy_overrides: dict | None = None  # L1/L2/L3 啟用/停用 sparse map
    # 概覽自訂看板（非機密）
    overview_boards: list[dict] | None = None  # [{id,label,chartIds[]}]
    active_overview_board_id: str | None = None


@app.get("/api/settings")
def get_settings(user: dict = Depends(load_user_context)) -> dict:
    """當前 user 的 LLM 模型配置（api_token 遮罩，附 has_token / stub_mode）。"""
    from app.core import settings as app_settings

    _activate_settings(user["user_id"])
    data = app_settings.masked(user["user_id"])
    data["stub_mode"] = llm_client.is_stub()
    return data


@app.post("/api/settings")
def update_settings(body: SettingsIn, user: dict = Depends(load_user_context)) -> dict:
    """更新當前 user 的模型配置（空/遮罩 token 不覆蓋既有）。"""
    from app.core import settings as app_settings

    data = app_settings.save_settings(user["user_id"], body.model_dump(exclude_none=True))
    _activate_settings(user["user_id"])  # 反映新 token（stub_mode）+ 新 taxonomy_overrides
    data["stub_mode"] = llm_client.is_stub()
    return data


@app.get("/api/settings/raw")
def get_settings_raw(user: dict = Depends(load_user_context)) -> dict:
    """當前 user 的完整配置（api_token 明文）——供設定面板眼睛切換顯示全文。

    ⚠️ 明文回傳 token：僅限受信任的本地 / 內網環境，勿暴露於公網。
    """
    from app.core import settings as app_settings

    _activate_settings(user["user_id"])
    data = app_settings.raw(user["user_id"])
    data["stub_mode"] = llm_client.is_stub()
    return data


class TestLlmIn(BaseModel):
    """即時測試 LLM 入參：當前表單 flat 值（非已儲存）；token 空/遮罩沿用已儲存該 provider token。"""

    base_url: str | None = None
    model: str | None = None
    temperature: float | None = None
    reasoning_effort: str | None = None
    provider_tokens: dict | None = None  # { provider_id: token }


@app.post("/api/settings/test-llm")
def test_llm(body: TestLlmIn, user: dict = Depends(load_user_context)) -> dict:
    """即時測試 LLM 連線：用「當前表單值」（body，非已儲存）送極短 prompt，**不寫入** user_settings。

    token 為空 / 遮罩時沿用已儲存該 provider 明文（免重輸）；以 base_url 反推 provider 取 token。
    """
    from app.core import settings as app_settings

    saved = app_settings.load_settings(user["user_id"])  # 含明文 provider_tokens
    # provider_tokens 逐 key 合併（空/遮罩 → 沿用已儲存該 provider token，免重輸）
    ptokens = dict(saved.get("provider_tokens") or {})
    for pid, tok in (body.provider_tokens or {}).items():
        if tok and "***" not in str(tok) and "…" not in str(tok):
            ptokens[pid] = tok
    base_url = (body.base_url or "").strip()
    cfg = {
        "token": ptokens.get(app_settings.provider_id_for(base_url)) or config.env.openai_api_key,
        "base_url": base_url,
        "model": body.model or config.env.ai_judge_model,
        "temperature": body.temperature,
        "reasoning_effort": body.reasoning_effort or "default",
    }
    return llm_client.ping(cfg=cfg)


def _model_meets_min(model_id: str, min_version: str) -> bool:
    """gpt-N.M 版本 ≥ min_version 才保留；非 gpt-* model（gemini/bytedance 等）不受版本限制。"""
    import re

    m = re.match(r"^gpt-(\d+)(?:\.(\d+))?", model_id)
    if not m:
        return True
    cur = (int(m.group(1)), int(m.group(2) or 0))
    mj, mn = (min_version.split(".") + ["0"])[:2]
    return cur >= (int(mj), int(mn))


@app.get("/api/settings/models")
def list_models(user: dict = Depends(load_user_context)) -> dict:
    """動態列出當前 user 配置可取得的 model id（過濾 ≥ 門檻版本）；無 token 回空清單。

    供前端 Model 下拉 Arco loading 更新；成本/評價由前端 config/global/llm_model.json defaultModels[].desc 提供（API 無此資訊）。
    """
    from app.core import settings as app_settings

    _activate_settings(user["user_id"])
    ids = llm_client.list_models()
    minv = app_settings.LLM_MODEL_MIN_VERSION
    return {"models": [m for m in ids if _model_meets_min(m, minv)]}


@app.get("/api/findings")
def get_findings(
    prod_oid: str | None = None,
    dimension: str | None = None,
) -> list[dict]:
    """列出判決結果（可依 prod_oid / dimension 過濾；下鑽用）。"""
    return db.list_findings(prod_oid, dimension)


@app.get("/api/products")
def get_products() -> list[dict]:
    """有 finding 的商品清單（PM 單品頁下拉）。"""
    return db.list_products()


@app.get("/api/problems")
def get_problems(
    source: str | None = None,
    judged: bool | None = None,
    polarity: str | None = None,
    stage: str | None = None,
    scores: str | None = None,
    product_verticals: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    prod_oid: str | None = None,
    order_oid: str | None = None,
    sort_by: str | None = None,
    sort_dir: str = "desc",
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """統一問題列表（intake + 歸因 即時 join，**伺服器端分頁**）。回 {rows, total}。

    公共欄位於回傳層由 source_mapping 從 raw 還原；judged 篩已/未歸因；polarity 篩傾向。
    星等 scores / 商品垂直分類 product_verticals 走前端 CSV（逗號串）傳入，此處拆回清單再轉 db。
    date_from/date_to 為 'YYYY-MM-DD' 區間（含端點）。星等/分類僅對有對應欄的來源（如 product_reviews）生效。
    prod_oid/order_oid 精確過濾；sort_by（occurred_at/score/go_date/confidence）+ sort_dir（asc/desc）動態排序，
    未指定或非白名單欄一律回退 occurred_at DESC；item_id tiebreaker（穩定·跨頁不變）。
    """
    return db.list_problems(
        source=source,
        judged=judged,
        polarity=polarity,
        stage=stage,
        score=_csv_ints(scores),
        product_vertical=_csv_strs(product_verticals),
        date_from=date_from,
        date_to=date_to,
        prod_oid=prod_oid,
        order_oid=order_oid,
        sort_by=sort_by,
        sort_dir=sort_dir,
        limit=limit,
        offset=offset,
    )


class ExportProblemsIn(BaseModel):
    """導出請求（POST：item_ids 可能上千筆，放 body 避免 GET URL 過長 414）。"""

    source: str | None = None
    polarity: str | None = None
    judged: bool | None = None
    item_ids: list[str] | None = None
    scores: list[int] | None = None
    product_verticals: list[str] | None = None
    date_from: str | None = None
    date_to: str | None = None


@app.post("/api/problems/export")
def export_problems(body: ExportProblemsIn) -> Response:
    """導出統一問題列表為**美化 xlsx**（全量·不受分頁限制；1:N 每條歸因一列、xid 第一列、不含 item_id）。

    item_ids 給定→只導那些 review（複選/分頁選取，可上千）；否則導符合 source/polarity/judged
    + 星等 / 商品垂直分類 / 日期區間 篩選（與列表頁一致，避免導出與畫面不同步）全部。
    """
    xlsx_bytes = db.export_problems_xlsx(
        source=body.source,
        polarity=body.polarity,
        judged=body.judged,
        item_ids=body.item_ids,
        score=body.scores,
        product_vertical=body.product_verticals,
        date_from=body.date_from,
        date_to=body.date_to,
    )
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="problems_{body.source or "all"}.xlsx"'
        },
    )


@app.get("/api/problems/summary")
def get_problems_summary() -> dict:
    """問題即時匯總（不另存匯總表）：來源 / 歸因域 / 信心分層 分佈。"""
    return db.problems_summary()


@app.get("/api/problems/attribution_overview")
def get_attribution_overview(
    source: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    granularity: str = "month",
    product_verticals: str | None = None,
) -> dict:
    """歸因縱覽聚合（縱覽頁專用）：KPI + 傾向/L1域/信心分層/星等 分布 + 趨勢。

    可選 date_from/date_to（'YYYY-MM-DD' 區間，含端點）與 granularity（year|month|day，趨勢粒度）；
    product_verticals（逗號串，全局商品垂直分類篩選；僅 product_reviews 生效）。
    """
    return db.attribution_overview(
        source=source,
        date_from=date_from,
        date_to=date_to,
        granularity=granularity,
        product_vertical=_csv_strs(product_verticals),
    )


@app.get("/api/problems/attribution_breakdown")
def get_attribution_breakdown(
    l1: str,
    source: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    product_verticals: str | None = None,
) -> dict:
    """某 L1 歸因域下的 L2/L3 細項分布（縱覽長條下鑽·懶載）；可選 date_from/date_to 區間 + 全局商品垂直分類。"""
    return db.attribution_breakdown(
        source=source,
        l1=l1,
        date_from=date_from,
        date_to=date_to,
        product_vertical=_csv_strs(product_verticals),
    )


class StatusIn(BaseModel):
    # 人工只可改這三態；new / data_missing 由系統設定。非法值 Pydantic 自動回 422。
    status: Literal["confirmed", "dismissed", "fixed"]


@app.patch("/api/findings/{finding_id}/status")
def patch_finding_status(finding_id: str, body: StatusIn) -> dict:
    """更新 Finding 狀態（出口A 確認/忽略/已修）。"""
    if not db.update_finding_status(finding_id, body.status):
        raise HTTPException(status_code=404, detail="finding not found")
    return {"finding_id": finding_id, "status": body.status}


# ── 預留路由 ───────────────────────────────────────────────────────
# L0 自動拉：fetch_product(live, api-b2c) · M3：/findings/export(xlsx)
# P2 多管道：order/工單 adapter + 聯合判定


# ── 資料來源：QC DB（PostgreSQL）連線測試 ─────────────────────────────
class QcDbTestIn(BaseModel):
    """測試連線入參（皆選填）；config_id 指定要測哪套（預設 active），空/遮罩 password 沿用既存明文。"""

    config_id: str | None = None  # 反查 qc_passwords 用；None → active_qc_config_id
    env: str | None = None
    host: str | None = None
    port: int | None = None
    names: list[str] | None = None
    schemas: list[str] | None = None
    user: str | None = None
    password: str | None = None


def _qc_db_bootstrap_name(cfg: dict) -> str:
    """決定測試連線/列舉 database 用的 bootstrap dbname。

    優先取已多選清單首項（已知可連的庫）；尚未選取時回退 env（sit/stage）的預設 database。
    PostgreSQL 連任一庫即可 SELECT pg_database 列出全部，故起手庫只需任選其一。
    """
    names = cfg.get("names") or []
    if names:
        return names[0]
    from app.core.settings import qc_db_env_name

    return qc_db_env_name(cfg.get("env"))


def _try_qc_db_connect(cfg: dict) -> dict:
    """以 cfg 連 QC DB（5s timeout）並列舉可用 database / schema，回 {ok, databases?, schemas?, error?}。

    不回傳含密碼的連線字串。連線成功後 SELECT pg_database（排除 template）+ information_schema.schemata
    （排除系統 schema）供前端多選載入。schema 為連線「起手庫」的清單（schema 屬 per-database，
    多選多庫時以起手庫為準；KKday 多數庫為 public，實務差異小）。
    """
    host = cfg.get("host") or ""
    if not host:
        return {"ok": False, "error": "未設定 host"}
    name = _qc_db_bootstrap_name(cfg)
    if not name:
        # 防呆：libpq 在 dbname 空時會預設用 username 當 database，產生誤導性 "database <user> does not exist"
        return {"ok": False, "error": "未設定 database name（請先選擇環境或輸入起手庫）"}
    try:
        import psycopg2  # 延遲匯入：未裝時不阻斷面板儲存
    except ImportError:
        return {
            "ok": False,
            "error": "後端未安裝 psycopg2，無法測試連線（pip install psycopg2-binary）",
        }
    from app.core.settings import (
        QC_DB_DEFAULTS,  # port fallback 取共用 config/global/qc_db.json
    )

    try:
        conn = psycopg2.connect(
            host=host,
            port=cfg.get("port") or QC_DB_DEFAULTS["port"],
            dbname=name,
            user=cfg.get("user") or "",
            password=cfg.get("password") or "",
            connect_timeout=config.env.qc_db_connect_timeout,
        )
        try:
            with conn.cursor() as cur:
                # 列舉非 template 的可連 database，供前端多選；排序穩定便於閱讀
                cur.execute(
                    "SELECT datname FROM pg_database "
                    "WHERE datistemplate = false AND datallowconn = true "
                    "ORDER BY datname"
                )
                databases = [r[0] for r in cur.fetchall()]
                # 列舉起手庫的使用者 schema（排除 pg_* / information_schema 系統 schema）
                cur.execute(
                    "SELECT schema_name FROM information_schema.schemata "
                    "WHERE schema_name NOT LIKE 'pg\\_%' "
                    "AND schema_name <> 'information_schema' "
                    "ORDER BY schema_name"
                )
                schemas = [r[0] for r in cur.fetchall()]
        finally:
            conn.close()
        return {"ok": True, "databases": databases, "schemas": schemas}
    except Exception as e:  # 只回錯誤首行（避免洩漏連線細節 / 密碼）
        return {"ok": False, "error": str(e).splitlines()[0][:200]}


@app.post("/api/datasource/qc-db/test")
def test_qc_db(body: QcDbTestIn, user: dict = Depends(load_user_context)) -> dict:
    """測試某套 QC DB 連線並列舉 database/schema：以指定 config（或 active）為底，body 覆蓋表單值。

    password 空/遮罩 → 反查 qc_passwords[config_id]（免重輸）；config_id 缺省取 active_qc_config_id。
    """
    from app.core import settings as app_settings

    saved = app_settings.load_settings(user["user_id"])
    cid = body.config_id or saved.get("active_qc_config_id")
    base = next((c for c in (saved.get("qc_configs") or []) if c.get("id") == cid), {})
    cfg = {k: base.get(k) for k in ("env", "host", "port", "user", "names", "schemas")}
    # body 表單值覆蓋（None 不覆蓋）
    for k in ("env", "host", "port", "user", "names", "schemas"):
        v = getattr(body, k, None)
        if v is not None:
            cfg[k] = v
    # password：body 明文優先；空/遮罩 → 沿用 qc_passwords[cid]
    pw = body.password
    if pw and "***" not in pw and "…" not in pw:
        cfg["password"] = pw
    else:
        cfg["password"] = (saved.get("qc_passwords") or {}).get(cid, "")
    return _try_qc_db_connect(cfg)


def _csv_ints(raw: str | None) -> list[int] | None:
    """把前端 CSV query（如 '1,5'）拆成 int 清單；空/無有效值回 None（等同不過濾）。

    非數字片段直接略過（防禦式），避免單一壞值讓整支查詢 422。
    """
    if not raw:
        return None
    out = [int(s) for s in raw.split(",") if s.strip().lstrip("-").isdigit()]
    return out or None


def _csv_strs(raw: str | None) -> list[str] | None:
    """把前端 CSV query（如 'Tour,Exp'）拆成去空白的字串清單；空回 None（不過濾）。"""
    if not raw:
        return None
    out = [s.strip() for s in raw.split(",") if s.strip()]
    return out or None
