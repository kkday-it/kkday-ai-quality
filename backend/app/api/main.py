"""kkday-ai-quality 後端入口（FastAPI）。

AI 商品質檢平台 — AI 法官（內容爭議裁決，內容質量 Pod 第三支柱）。
本檔僅負責 app 組裝：middleware + 建表/播種 + 掛載各領域 router；端點實作分散於 `app/api/routers/`
（auth / inbound / settings / findings / problems / v1 / rules / exports 各自帶完整 /api 路徑）。

啟動：uvicorn app.api.main:app --reload --port 8100
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# config 先載入：import 時即讀 backend/.env 並建 env 單例（機密集中管理）。
from app.core import config, db  # noqa: F401

app = FastAPI(title="kkday-ai-quality", version="0.0.1")

app.add_middleware(
    CORSMiddleware,
    # 部署換 domain / 加 staging 免改碼：env CORS_ALLOW_ORIGINS 逗號分隔（預設對齊 vite dev 5273）
    allow_origins=[o.strip() for o in config.env.cors_allow_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)

db.init_db()  # 啟動即建表（冪等）

# ── 掛載領域 router（各自帶完整 /api 路徑；v1 為新攝取架構 /api/v1）──
from app.api.routers import admin_import as admin_import_router  # noqa: E402
from app.api.routers import auth as auth_router  # noqa: E402
from app.api.routers import exports as exports_router  # noqa: E402
from app.api.routers import findings as findings_router  # noqa: E402
from app.api.routers import inbound as inbound_router  # noqa: E402
from app.api.routers import llm_usage as llm_usage_router  # noqa: E402
from app.api.routers import overview as overview_router  # noqa: E402
from app.api.routers import problems as problems_router  # noqa: E402
from app.api.routers import rules as rules_router  # noqa: E402
from app.api.routers import settings as settings_router  # noqa: E402
from app.api.routers import (  # noqa: E402
    v1_router,
)

for _r in (
    v1_router,  # /api/v1（攝取 + 初判）
    rules_router.router,  # /api/judge-rules（規則版本化）
    exports_router.router,  # /api/exports（通用導出 job）
    auth_router.router,  # /api/auth
    inbound_router.router,  # /api/inbound + /api/batches
    settings_router.router,  # /api/settings + /api/datasource
    findings_router.router,  # /api/findings + /api/products
    problems_router.router,  # /api/problems
    llm_usage_router.router,  # /api/llm-usage（AI 消耗聚合）
    overview_router.router,  # /api/overview（質檢概覽真實指標·縮窄真接）
    admin_import_router.router,  # /api/admin/import（全庫資料包安全匯入）
):
    app.include_router(_r)

db.seed_rules_from_files()  # 初次播種：無 DB 版的 rule 以默認檔建 v1 active（冪等）


@app.get("/api/status")
def status() -> dict[str, str]:
    """公司健康檢查契約：`GET /api/status` → `{"status":"0000","message":"success"}`。

    對齊 KKday EKS 上線驗證與 k8s readiness probe 慣例（路徑固定 /api/status、
    不掛認證——本專案認證走 per-route Depends 非全域 middleware，新端點天然免 auth）。
    access log 排除見 logging_setup 的 uvicorn.access filter（避免 probe 噪音進 Kibana）。
    """
    return {"status": "0000", "message": "success"}
