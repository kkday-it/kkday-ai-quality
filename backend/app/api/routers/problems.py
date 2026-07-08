"""統一問題列表 + 歸因概覽聚合 + 列表導出端點；全路徑自帶 /api。"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.core import db

router = APIRouter()


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


@router.get("/api/problems")
def get_problems(
    source: str | None = None,
    judged: bool | None = None,
    polarity: str | None = None,
    sentiment: str | None = None,
    stage: str | None = None,
    scores: str | None = None,
    product_verticals: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    rec_oid: str | None = None,
    prod_oid: str | None = None,
    order_oid: str | None = None,
    confidence_tier: str | None = None,
    l1_domain: str | None = None,
    has_external: bool | None = None,
    sort_by: str | None = None,
    sort_dir: str = "desc",
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """統一問題列表（intake + 歸因 即時 join，**伺服器端分頁**）。回 {rows, total}。

    公共欄位於回傳層由 source_mapping 從 raw 還原；judged 篩已/未歸因；polarity 篩傾向。
    星等 scores / 商品垂直分類 product_verticals / 判決階段 stage 走前端 CSV（逗號串）傳入，此處拆回清單再轉 db。
    confidence_tier（信心分層）/ l1_domain（L1 歸因域）為單值 judgments.data 過濾。
    has_external：有無外部評論融合資料（true/false；缺省＝全部，僅 product_reviews 生效）。
    date_from/date_to 為 'YYYY-MM-DD' 區間（含端點）。星等/分類僅對有對應欄的來源（如 product_reviews）生效。
    rec_oid（評論 id，各來源表 natural_key）/prod_oid/order_oid 精確過濾；sort_by（occurred_at/score/go_date/confidence）+ sort_dir（asc/desc）動態排序，
    未指定或非白名單欄一律回退 occurred_at DESC；item_id tiebreaker（穩定·跨頁不變）。
    """
    return db.list_problems(
        source=source,
        judged=judged,
        polarity=_csv_strs(polarity),
        sentiment=_csv_ints(sentiment),
        stage=_csv_strs(stage),
        score=_csv_ints(scores),
        product_vertical=_csv_strs(product_verticals),
        date_from=date_from,
        date_to=date_to,
        rec_oid=rec_oid,
        prod_oid=prod_oid,
        order_oid=order_oid,
        confidence_tier=confidence_tier,
        l1_domain=l1_domain,
        has_external=has_external,
        sort_by=sort_by,
        sort_dir=sort_dir,
        limit=limit,
        offset=offset,
    )


@router.get("/api/problems/l1_domains")
def get_l1_domains(source: str) -> list[dict]:
    """某來源已判資料出現過的 L1 歸因域清單（[{code,label,count}]）——供歸因列表 L1 篩選下拉。

    選項直接來自 judgments.data distinct，恆與可篩內容一致（見 db.list_l1_domains）。
    """
    return db.list_l1_domains(source)


class ExportProblemsIn(BaseModel):
    """導出請求（POST：item_ids 可能上千筆，放 body 避免 GET URL 過長 414）。"""

    source: str | None = None
    polarity: str | list[str] | None = None
    judged: bool | None = None
    item_ids: list[str] | None = None
    scores: list[int] | None = None
    product_verticals: list[str] | None = None
    date_from: str | None = None
    date_to: str | None = None
    # 與列表頁篩選對齊（導出＝所見即所得）：情緒分 / 判決階段 / 信心分層 / L1 域 / 有無外部評論 / 精確 id
    sentiment: list[int] | None = None
    stage: list[str] | None = None
    confidence_tier: str | None = None
    l1_domain: str | None = None
    has_external: bool | None = None
    rec_oid: str | None = None
    prod_oid: str | None = None
    order_oid: str | None = None


@router.post("/api/problems/export")
def export_problems(body: ExportProblemsIn) -> dict:
    """啟動問題列表導出背景 job → {job_id, filename}（立即回，背景組檔）。

    大列表組 xlsx 可能耗時數十秒，改背景 job：前端連 SSE（/api/exports/stream）看進度、可停止，
    完成後 /api/exports/download 取檔。item_ids 給定→只導那些 review（複選/分頁選取，可上千）；
    否則導符合 source/polarity/judged + 星等 / 商品垂直分類 / 日期區間 篩選（與列表頁一致）全部。
    """
    from app.core import export_jobs

    def _builder(ctx: export_jobs.ExportCtx) -> bytes:
        """背景組檔：逐 review 回報進度 + 輪詢取消（ctx 穿透至 db 層 fan-out 迴圈）。"""
        return db.export_problems_xlsx(
            source=body.source,
            polarity=body.polarity,
            judged=body.judged,
            item_ids=body.item_ids,
            score=body.scores,
            product_vertical=body.product_verticals,
            date_from=body.date_from,
            date_to=body.date_to,
            sentiment=body.sentiment,
            stage=body.stage,
            confidence_tier=body.confidence_tier,
            l1_domain=body.l1_domain,
            has_external=body.has_external,
            rec_oid=body.rec_oid,
            prod_oid=body.prod_oid,
            order_oid=body.order_oid,
            ctx=ctx,
        )

    filename = f"problems_{body.source or 'all'}.xlsx"
    job_id = export_jobs.start_export(_builder, filename)
    return {"job_id": job_id, "filename": filename}


@router.get("/api/problems/attribution_overview")
def get_attribution_overview(
    source: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    granularity: str = "month",
    product_verticals: str | None = None,
) -> dict:
    """歸因概覽聚合（概覽頁專用）：KPI + 傾向/L1域/信心分層/星等 分布 + 趨勢。

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


@router.get("/api/problems/attribution_breakdown")
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
