"""kkday-ai-product-quality 後端入口（FastAPI）。

AI 商品質檢平台 — AI 法官（內容爭議裁決，內容質量 Pod 第三支柱）。
本階段已實作：L1 資料錄入（CSV/Excel 批量 + 單個）→ 本地 SQLite。
判決層 L2–L4 待 OpenAI key（沿用 ProductContentAIChecker 資產）。

啟動：uvicorn app.api.main:app --reload --port 8100
"""

from __future__ import annotations

from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.core import db
from app.judge import pipeline
from app.judge.datasource import reviews
from app.judge.ingest import entry
from app.judge.llm import client as llm_client

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


# 來源標記 → 顯示名（用於批次自動命名「售前售後進線 2026062301」）
SOURCE_LABELS: dict[str, str] = {
    "presale_postsale": "售前售後進線",
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

    source：資料來源標記（presale_postsale 售前售後進線 / review 評論…）。
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
def diagnose(body: DiagnoseIn) -> dict:
    """評論線判決：拉差評 → classify→adequacy→arbiter→diagnose → 存 Finding。

    無 OpenAI key 時走 stub（啟發式）；key 到位自動切真 LLM。
    """
    tickets = reviews.fetch_reviews(body.prod_oid, source=body.source)
    findings = pipeline.diagnose_many(tickets, prod_source=body.source)
    db.insert_findings_batch(findings)
    return {
        "prod_oid": body.prod_oid,
        "count": len(findings),
        "stub_mode": llm_client.is_stub(),
        "findings": [f.model_dump() for f in findings],
    }


class IntakeIn(BaseModel):
    source: str = "fixture"  # fixture(MVP) | live(BigQuery，待權限)


@app.post("/api/diagnose/presale-postsale")
def diagnose_presale_postsale(body: IntakeIn) -> dict:
    """售前售後進線判定鏈路（第一階段主力管道）。

    adapter（售前 freshdesk + 售後 order_message/chatbot）→ NormalizedTicket
    → classify→adequacy→arbiter→diagnose → TicketFinding（含客服對話 ground truth）。
    評論/工單 API 列後續迭代。
    """
    from app.judge.ingest import presale_postsale

    tickets = presale_postsale.fetch_presale_postsale(source=body.source)
    findings = pipeline.diagnose_many(tickets, prod_source=body.source)
    db.insert_findings_batch(findings)
    return {
        "channel": "presale_postsale",
        "count": len(findings),
        "stub_mode": llm_client.is_stub(),
        "findings": [f.model_dump() for f in findings],
    }


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
    status: str  # confirmed | dismissed | fixed


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
