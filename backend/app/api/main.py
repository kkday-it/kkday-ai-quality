"""kkday-ai-quality 後端入口（FastAPI）。

AI 商品質檢平台 — AI 法官（內容爭議裁決，內容質量 Pod 第三支柱）。
本階段已實作：L1 資料錄入（CSV/Excel 批量 + 單個）→ 本地 SQLite。
判決層 L2–L4 待 OpenAI key（沿用 ProductContentAIChecker 資產）。

啟動：uvicorn app.api.main:app --reload --port 8100
"""

from __future__ import annotations

import json
import uuid
from typing import Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# config 先載入：import 時即讀 backend/.env 並建 env 單例（機密集中管理）。
from app.core import auth, config, db  # noqa: F401  (config import 觸發 .env 載入)
from app.core import source_mapping as srcmap
from app.judge.ingest import entry
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
    if "@" not in email or len(body.password) < 6:
        raise HTTPException(status_code=400, detail="email 格式錯誤或密碼少於 6 碼")
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


# 來源標記 → 顯示名（用於批次自動命名「售前售後進線 2026062301」）
SOURCE_LABELS: dict[str, str] = {
    "conversations": "售前售後進線",
    "review": "商品評論",
    "ticket": "工單",
    "manual": "其他",
    "csv": "CSV",
}


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
    """確認匯入：只匯入用戶勾選且校驗通過的工作表 → 正規化 → intake_items（冪等）。

    selections：JSON 字串 `[{"sheet_name","source"}]`（來自 /validate 後用戶勾選）。
    每張工作表建一個批次（label 取來源中文名）；逐列經 source_mapping 正規化落庫。
    """
    content = await file.read()
    try:
        sel = [UploadSelection(**s) for s in json.loads(selections)]
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=f"selections 格式錯誤：{e}") from None
    if not sel:
        raise HTTPException(status_code=400, detail="未選擇任何工作表")
    sheets = {sh["sheet_name"]: sh for sh in entry.read_sheets(content, file.filename or "")}
    results = []
    for s in sel:
        sh = sheets.get(s.sheet_name)
        if sh is None:
            results.append({"sheet_name": s.sheet_name, "source": s.source, "error": "工作表不存在"})
            continue
        missing = srcmap.validate_headers(s.source, sh["headers"])
        if missing:
            results.append(
                {"sheet_name": s.sheet_name, "source": s.source, "error": f"缺必備欄：{'、'.join(missing)}"}
            )
            continue
        items = [entry.item_from_canonical(srcmap.normalize_row(s.source, row), row) for row in sh["rows"]]
        label = srcmap.source_label(s.source)
        batch = db.create_batch(s.source, label, f"{file.filename}::{s.sheet_name}", len(items), len(items))
        for it in items:
            it.batch_id = batch["batch_id"]
        inserted = db.insert_inbound_batch(items)
        results.append(
            {
                "sheet_name": s.sheet_name, "source": s.source, "label": label,
                "batch_id": batch["batch_id"], "inserted": inserted, "total": len(items),
            }
        )
    return {"results": results}


@app.post("/api/inbound")
def add_inbound(body: SingleEntryIn) -> dict:
    """單個新增 → 存 SQLite。"""
    item = entry.single_entry(body.prod_oid, body.comment, body.rating, body.pkg_oid)
    db.insert_inbound(item)
    return item.model_dump()


@app.get("/api/inbound")
def get_inbound(status: str | None = None) -> list[dict]:
    """列出錄入標的（可依 status 過濾），新到舊。"""
    return db.list_inbound(status)


@app.get("/api/batches")
def get_batches() -> list[dict]:
    """上傳批次清單（新到舊）。"""
    return db.list_batches()


@app.get("/api/batches/{batch_id}/items")
def get_batch_items(batch_id: str) -> list[dict]:
    """某批次的錄入明細（點擊表格展示用）。"""
    return db.list_inbound(batch_id=batch_id)


@app.get("/api/batches/{batch_id}/export")
def export_batch(batch_id: str) -> Response:
    """匯出批次明細為 CSV 下載。"""
    csv_bytes = db.export_inbound_csv(batch_id)
    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{batch_id}.csv"'},
    )


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

    供前端 Model 下拉 Arco loading 更新；成本/評價由前端 config/global/default_llm.json defaultModels[].desc 提供（API 無此資訊）。
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
    verdict: str | None = None,
) -> list[dict]:
    """列出判決結果（可依 prod_oid / dimension / verdict 過濾；下鑽用）。"""
    return db.list_findings(prod_oid, dimension, verdict)


@app.get("/api/products")
def get_products() -> list[dict]:
    """有 finding 的商品清單（PM 單品頁下拉）。"""
    return db.list_products()


@app.get("/api/problems")
def get_problems(
    source: str | None = None,
    verdict: str | None = None,
    judged: bool | None = None,
    polarity: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """統一問題列表（intake + 歸因 即時 join，**伺服器端分頁**）。回 {rows, total}。

    公共欄位於回傳層由 source_mapping 從 raw 還原；judged 篩已/未歸因；polarity 篩傾向。
    排序：評論時間 occurred_at DESC（新在前）+ item_id tiebreaker（穩定·跨頁不變）。
    """
    return db.list_problems(
        source=source, verdict=verdict, judged=judged, polarity=polarity, limit=limit, offset=offset
    )


class ExportProblemsIn(BaseModel):
    """導出請求（POST：item_ids 可能上千筆，放 body 避免 GET URL 過長 414）。"""

    source: str | None = None
    polarity: str | None = None
    judged: bool | None = None
    item_ids: list[str] | None = None


@app.post("/api/problems/export")
def export_problems(body: ExportProblemsIn) -> Response:
    """導出統一問題列表 CSV（全量·不受分頁限制；欄位齊全）。

    item_ids 給定→只導那些（複選/分頁選取，可上千）；否則導符合 source/polarity/judged 全部。
    """
    csv_bytes = db.export_problems_csv(
        source=body.source, polarity=body.polarity, judged=body.judged, item_ids=body.item_ids
    )
    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="problems_{body.source or "all"}.csv"'},
    )


@app.get("/api/problems/summary")
def get_problems_summary() -> dict:
    """問題即時匯總（不另存匯總表）：來源 / verdict / 歸因域 / 信心分層 分佈。"""
    return db.problems_summary()


@app.get("/api/problems/attribution_overview")
def get_attribution_overview(source: str | None = None) -> dict:
    """歸因縱覽聚合（縱覽頁專用）：KPI + 傾向/L1域/判決/信心分層/星等 分布 + 月趨勢。"""
    return db.attribution_overview(source=source)


@app.get("/api/problems/attribution_breakdown")
def get_attribution_breakdown(l1: str, source: str | None = None) -> dict:
    """某 L1 歸因域下的 L2/L3 細項分布（縱覽長條下鑽·懶載）。"""
    return db.attribution_breakdown(source=source, l1=l1)


class StatusIn(BaseModel):
    # 人工只可改這三態；new / data_missing 由系統設定。非法值 Pydantic 自動回 422。
    status: Literal["confirmed", "dismissed", "fixed"]


@app.get("/api/findings/aggregate")
def findings_aggregate() -> dict:
    """dimension×verdict 熱力矩陣聚合 + KPI（出口B 用）。"""
    return db.aggregate_findings()


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
        QC_DB_DEFAULTS,  # port fallback 取共用 config/global/default_qc.json
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
