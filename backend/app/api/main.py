"""kkday-ai-quality 後端入口（FastAPI）。

AI 商品質檢平台 — AI 法官（內容爭議裁決，內容質量 Pod 第三支柱）。
本檔僅負責 app 組裝：middleware + 建表/播種 + 掛載各領域 router；端點實作分散於 `app/api/routers/`
（auth / inbound / settings / findings / problems / v1 / rules / exports 各自帶完整 /api 路徑）。

啟動：uvicorn app.api.main:app --reload --port 8100
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# config 先載入：import 時即讀 backend/.env 並建 env 單例（機密集中管理）。
from app.core import config, db  # noqa: F401
from app.core.logging_setup import RequestContextMiddleware, configure_logging
from app.core.shutdown import mark_running_jobs_interrupted

# kklog JSON stdout：於 app 模組載入時套用（uvicorn 自身 dictConfig 先跑、本配置後蓋前）。
configure_logging()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """graceful shutdown：SIGTERM → uvicorn 停收新請求 → 本收尾標記進行中 job → drain in-flight。

    timeout 鏈（三層遞增，改任一須同步他層）：uvicorn `--timeout-graceful-shutdown 30`
    （Dockerfile CMD）< docker `stop_grace_period 35s`（docker-compose.yml）< k8s
    `terminationGracePeriodSeconds 40`（deploy/base/backend-deployment.yaml）。
    """
    yield
    mark_running_jobs_interrupted()


def docs_kwargs() -> dict:
    """production 收斂 API schema 面：關閉 /docs /redoc /openapi.json。

    內部 private ALB 之上再加一層——完整 API schema 不對未認證流量公開；dev/SIT 保留供除錯。
    """
    if config.is_production():
        return {"docs_url": None, "redoc_url": None, "openapi_url": None}
    return {}


app = FastAPI(title="kkday-ai-quality", version="0.0.1", lifespan=lifespan, **docs_kwargs())

# 每請求 X-Request-Id 關聯鍵（kklog request.uuid）。先 add＝內層——CORS 須最外層
# （add_middleware 為 stack，後 add 者在外），錯誤回應才保有 CORS header。
app.add_middleware(RequestContextMiddleware)
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

# Prometheus /metrics（EKS Step 6 Grafana 驗收契約；PHP fpm-exporter 的 Python 等效）。
# 免 auth（Prometheus scrape 不帶憑證）、不進 OpenAPI schema、access log 排除見 logging_setup。
from prometheus_fastapi_instrumentator import Instrumentator  # noqa: E402

Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)


@app.get("/api/status")
def status() -> dict[str, str]:
    """公司健康檢查契約：`GET /api/status` → `{"status":"0000","message":"success"}`。

    對齊 KKday EKS 上線驗證與 k8s readiness probe 慣例（路徑固定 /api/status、
    不掛認證——本專案認證走 per-route Depends 非全域 middleware，新端點天然免 auth）。
    access log 排除見 logging_setup 的 uvicorn.access filter（避免 probe 噪音進 Kibana）。
    """
    return {"status": "0000", "message": "success"}
