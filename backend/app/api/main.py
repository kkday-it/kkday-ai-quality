"""kkday-ai-product-quality 後端入口（FastAPI）。

AI 商品質檢平台 — AI 法官（內容爭議裁決，內容質量 Pod 第三支柱）。
本階段已實作：L1 資料錄入（CSV/Excel 批量 + 單個）→ 本地 SQLite。
判決層 L2–L4 待 OpenAI key（沿用 ProductContentAIChecker 資產）。

啟動：uvicorn app.api.main:app --reload --port 8100
"""

from __future__ import annotations

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.core import db
from app.judge.ingest import entry

app = FastAPI(title="kkday-ai-product-quality", version="0.0.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

db.init_db()  # 啟動即建表（冪等）


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# ── L1 資料錄入（CSV/Excel 批量 + 單個）→ 本地 SQLite ──────────────────
class SingleEntryIn(BaseModel):
    prod_oid: str
    comment: str
    rating: int | None = None
    pkg_oid: str = ""


@app.post("/api/inbound/upload")
async def upload_inbound(file: UploadFile = File(...)) -> dict:
    """CSV/Excel 批量錄入 → 解析 → 存 SQLite（冪等去重）。"""
    content = await file.read()
    name = (file.filename or "").lower()
    if name.endswith(".csv"):
        items = entry.parse_csv(content)
    elif name.endswith((".xlsx", ".xls")):
        items = entry.parse_excel(content)
    else:
        raise HTTPException(status_code=400, detail="只支援 .csv / .xlsx")
    inserted = db.insert_inbound_batch(items)
    return {"inserted": inserted, "preview": [i.model_dump() for i in items[:5]]}


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


# ── 預留路由 ───────────────────────────────────────────────────────
# L0/L1 自動拉（M1）：GET /api/product/{id}/reviews · /config
# L2–L4 判決（M2，需 OpenAI key）：POST /api/diagnose  inbound → TicketFinding[]
# L5 dashboard（M3）：GET /api/findings · /aggregate · /export · PATCH status
