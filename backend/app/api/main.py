"""kkday-ai-product-quality 後端入口（FastAPI）。

AI 商品質檢平台 — AI 法官（內容爭議裁決，內容質量 Pod 第三支柱）。
本階段已實作：L1 資料錄入（CSV/Excel 批量 + 單個）→ 本地 SQLite。
判決層 L2–L4 待 OpenAI key（沿用 ProductContentAIChecker 資產）。

啟動：uvicorn app.api.main:app --reload --port 8100
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# config 先載入：import 時即讀 backend/.env 並建 env 單例（機密集中管理）。
from app.core import auth, config, db  # noqa: F401  (config import 觸發 .env 載入)
from app.judge import pipeline
from app.judge.datasource import reviews
from app.judge.ingest import entry
from app.judge.llm import client as llm_client

app = FastAPI(title="kkday-ai-product-quality", version="0.0.1")

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
    except sqlite3.IntegrityError:
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

    app_settings.set_current(app_settings.load_settings(user_id))


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


@app.post("/api/inbound/upload")
async def upload_inbound(
    file: UploadFile = File(...),
    source: str = Form("csv"),
) -> dict:
    """CSV/Excel 批量錄入 → 建批次（自動命名）→ 解析存 SQLite（冪等去重）。

    source：資料來源標記（conversations 售前售後進線 / review 評論…）。
    每次上傳產生一筆 upload_batch，明細 inbound_items 以 batch_id 關聯。
    """
    content = await file.read()
    fname = file.filename or ""
    name = fname.lower()
    if name.endswith(".csv"):
        items = entry.parse_csv(content, source)
    elif name.endswith((".xlsx", ".xls")):
        items = entry.parse_excel(content, source)
    else:
        raise HTTPException(status_code=400, detail="只支援 .csv / .xlsx")
    label = SOURCE_LABELS.get(source, source)
    batch = db.create_batch(source, label, fname, len(items), len(items))
    for it in items:
        it.batch_id = batch["batch_id"]
    inserted = db.insert_inbound_batch(items)
    return {
        "batch": batch,
        "inserted": inserted,
        "total": len(items),
        "source": source,
        "preview": [i.model_dump() for i in items[:5]],
    }


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


# ── L2–L4 判決（評論線）+ L5 結果查詢 ─────────────────────────────
class DiagnoseIn(BaseModel):
    prod_oid: str
    source: str = "fixture"  # fixture(MVP) | live(production)


@app.post("/api/diagnose")
def diagnose(body: DiagnoseIn, user: dict = Depends(load_user_context)) -> dict:
    """評論線判決：拉差評 → classify→adequacy→arbiter→diagnose → 存 Finding。

    無 OpenAI key 時走 stub（啟發式）；key 到位自動切真 LLM。
    """
    _activate_settings(user["user_id"])  # judge 路徑用登入者的 key/model
    tickets = reviews.fetch_reviews(body.prod_oid, source=body.source)
    findings = pipeline.diagnose_many(tickets, prod_source=body.source)
    db.insert_findings_batch(findings)
    return {
        "prod_oid": body.prod_oid,
        "count": len(findings),
        "failed_count": len(tickets)
        - len(findings),  # 單筆判決失敗被略過的筆數（非 0 → 有靜默失敗，查 log）
        "stub_mode": llm_client.is_stub(),
        "findings": [f.model_dump() for f in findings],
    }


class IntakeIn(BaseModel):
    source: str = "fixture"  # fixture(MVP) | live(BigQuery，待權限)


@app.post("/api/diagnose/conversations")
def diagnose_conversations(body: IntakeIn, user: dict = Depends(load_user_context)) -> dict:
    """售前售後進線判定鏈路（第一階段主力管道）。

    adapter（售前 freshdesk + 售後 order_message/chatbot）→ NormalizedTicket
    → classify→adequacy→arbiter→diagnose → TicketFinding（含客服對話 ground truth）。
    評論/工單 API 列後續迭代。
    """
    from app.judge.ingest import conversations

    _activate_settings(user["user_id"])  # judge 路徑用登入者的 key/model
    tickets = conversations.fetch_conversations(source=body.source)
    findings = pipeline.diagnose_many(tickets, prod_source=body.source)
    db.insert_findings_batch(findings)
    return {
        "channel": "conversations",
        "count": len(findings),
        "stub_mode": llm_client.is_stub(),
        "findings": [f.model_dump() for f in findings],
    }


class SettingsIn(BaseModel):
    provider: str | None = None
    model: str | None = None
    base_url: str | None = None
    api_token: str | None = None
    temperature: float | None = None
    thinking: str | None = None
    reasoning_effort: str | None = None
    provider_models: dict | None = None  # 各供應商自訂 model 清單（per-user）
    stage_overrides: dict | None = None  # 各判決階段 LLM 覆寫（稀疏；後端 sanitize）
    # ── 資料來源：QC DB（PostgreSQL）連線配置（qc_db_password 機密，遮罩同 api_token）──
    qc_db_host: str | None = None
    qc_db_port: int | None = None
    qc_db_name: str | None = None
    qc_db_schema: str | None = None
    qc_db_user: str | None = None
    qc_db_password: str | None = None


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
    app_settings.set_current(
        app_settings.load_settings(user["user_id"])
    )  # 反映新 token，stub_mode 才正確
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


@app.post("/api/settings/test-llm")
def test_llm(user: dict = Depends(load_user_context)) -> dict:
    """測試 LLM 連線：以當前 user 已儲存的設定送一個極短 prompt，回終端機顯示用的 I/O。"""
    _activate_settings(user["user_id"])  # 載入該 user 的 token/model 到 contextvar 供 ping 讀取
    return llm_client.ping()


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

    供前端 Model 下拉 Arco loading 更新；成本/評價由前端 config/defaults.json modelMeta 提供（API 無此資訊）。
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
    """測試連線入參（皆選填）；空/遮罩 password 沿用後端既存明文值。"""

    qc_db_host: str | None = None
    qc_db_port: int | None = None
    qc_db_name: str | None = None
    qc_db_schema: str | None = None
    qc_db_user: str | None = None
    qc_db_password: str | None = None


def _try_qc_db_connect(cfg: dict) -> dict:
    """以 cfg 連 QC DB（5s timeout），回 {ok, error?}。不回傳含密碼的連線字串。"""
    host = cfg.get("qc_db_host") or ""
    if not host:
        return {"ok": False, "error": "未設定 host"}
    name = cfg.get("qc_db_name") or ""
    if not name:
        # 防呆：libpq 在 dbname 空時會預設用 username 當 database，產生誤導性 "database <user> does not exist"
        return {"ok": False, "error": "未設定 database name"}
    try:
        import psycopg2  # 延遲匯入：未裝時不阻斷面板儲存
    except ImportError:
        return {
            "ok": False,
            "error": "後端未安裝 psycopg2，無法測試連線（pip install psycopg2-binary）",
        }
    from app.core.settings import QC_DB_DEFAULTS  # port fallback 取共用 config/defaults.json

    try:
        conn = psycopg2.connect(
            host=host,
            port=cfg.get("qc_db_port") or QC_DB_DEFAULTS["port"],
            dbname=name,
            user=cfg.get("qc_db_user") or "",
            password=cfg.get("qc_db_password") or "",
            connect_timeout=config.env.qc_db_connect_timeout,
        )
        conn.close()
        return {"ok": True}
    except Exception as e:  # 只回錯誤首行（避免洩漏連線細節 / 密碼）
        return {"ok": False, "error": str(e).splitlines()[0][:200]}


@app.post("/api/datasource/qc-db/test")
def test_qc_db(body: QcDbTestIn, user: dict = Depends(load_user_context)) -> dict:
    """測試當前 user 的 QC DB 連線：以後端既存設定為底，前端傳入值覆蓋（遮罩/空 password 不覆蓋）。"""
    from app.core import settings as app_settings

    cfg = app_settings.load_settings(user["user_id"])
    ov = body.model_dump(exclude_none=True)
    pw = ov.pop("qc_db_password", None)
    cfg.update(ov)
    if pw and "***" not in pw and "…" not in pw:
        cfg["qc_db_password"] = pw
    return _try_qc_db_connect(cfg)
