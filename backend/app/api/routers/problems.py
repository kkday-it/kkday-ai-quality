"""統一問題列表 + 歸因概覽聚合 + 列表導出端點；全路徑自帶 /api。"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core import auth, db
from app.core.permissions import permission_keys, require_permission

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
    product_verticals: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    rec_oid: str | None = None,
    prod_oid: str | None = None,
    order_oid: str | None = None,
    confidence_tier: str | None = None,
    taxonomy: str | None = None,
    status: str | None = None,
    model: str | None = None,
    has_external: bool | None = None,
    sort_by: str | None = None,
    sort_dir: str = "desc",
    limit: int = 100,
    offset: int = 0,
    _user: dict = Depends(auth.get_current_user),
) -> dict:
    """統一問題列表（intake + 歸因 即時 join，**伺服器端分頁**）。回 {rows, total}。

    公共欄位於回傳層由 source_mapping 從 raw 還原；judged 篩已/未歸因；polarity 篩傾向。
    商品垂直分類 product_verticals / 判決階段 stage / 歸因分類 taxonomy 走前端 CSV（逗號串）傳入，此處拆回清單再轉 db。
    confidence_tier（信心分層）為單值、taxonomy（歸因分類，任意層級 code 多選，l1/l2_code 任一 IN 命中＝子樹語義）為多值判決過濾。
    status（覆核狀態 CSV 多選：new/auto_confirmed/confirmed/dismissed；任一歸因命中即列出）。
    model（判決模型 CSV 多選：judgments.model IN——當前判決維度）。
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
        product_vertical=_csv_strs(product_verticals),
        date_from=date_from,
        date_to=date_to,
        rec_oid=rec_oid,
        prod_oid=prod_oid,
        order_oid=order_oid,
        confidence_tier=confidence_tier,
        taxonomy=_csv_strs(taxonomy),
        status=_csv_strs(status),
        model=_csv_strs(model),
        has_external=has_external,
        sort_by=sort_by,
        sort_dir=sort_dir,
        limit=limit,
        offset=offset,
    )


class ExportProblemsIn(BaseModel):
    """導出請求（POST：item_ids 可能上千筆，放 body 避免 GET URL 過長 414）。"""

    source: str | None = None
    polarity: str | list[str] | None = None
    judged: bool | None = None
    item_ids: list[str] | None = None
    product_verticals: list[str] | None = None
    date_from: str | None = None
    date_to: str | None = None
    # 與列表頁篩選對齊（導出＝所見即所得）：情緒分 / 判決階段 / 信心分層 / 歸因分類 / 有無外部評論 / 精確 id
    sentiment: list[int] | None = None
    stage: list[str] | None = None
    confidence_tier: str | None = None
    taxonomy: list[str] | None = None
    status: list[str] | None = None
    # 判決模型篩選（當前判決維度，圈選哪些評論）；snapshot_model＝輸出結果版本（內容替換成
    # 該模型的 judgment_history 最新快照）——兩者語義獨立，可並用。
    model: list[str] | None = None
    snapshot_model: str | None = None
    # 並排對比模型（可複選）：每模型在基準右側附一組欄「情緒·M/L1·M/L2·M」，值取該模型最新快照。
    compare_models: list[str] | None = None
    has_external: bool | None = None
    rec_oid: str | None = None
    prod_oid: str | None = None
    order_oid: str | None = None


@router.post("/api/problems/export")
def export_problems(
    body: ExportProblemsIn,
    _user: dict = Depends(require_permission(permission_keys.PROBLEM_LIST_EXPORT)),
) -> dict:
    """啟動問題列表導出背景 job → {job_id, filename}（立即回，背景組檔）；需 problem.list.export 權限。

    大列表組 xlsx 可能耗時數十秒，改背景 job：前端連 SSE（/api/exports/stream）看進度、可停止，
    完成後 /api/exports/download 取檔。item_ids 給定→只導那些 review（複選/分頁選取，可上千）；
    否則導符合 source/polarity/judged + 商品垂直分類 / 歸因分類 / 日期區間 篩選（與列表頁一致）全部。
    """
    from app.core import export_jobs

    def _builder(ctx: export_jobs.ExportCtx) -> bytes:
        """背景組檔：逐 review 回報進度 + 輪詢取消（ctx 穿透至 db 層 fan-out 迴圈）。"""
        return db.export_problems_xlsx(
            source=body.source,
            polarity=body.polarity,
            judged=body.judged,
            item_ids=body.item_ids,
            product_vertical=body.product_verticals,
            date_from=body.date_from,
            date_to=body.date_to,
            sentiment=body.sentiment,
            stage=body.stage,
            confidence_tier=body.confidence_tier,
            taxonomy=body.taxonomy,
            status=body.status,
            model=body.model,
            snapshot_model=body.snapshot_model,
            compare_models=body.compare_models,
            has_external=body.has_external,
            rec_oid=body.rec_oid,
            prod_oid=body.prod_oid,
            order_oid=body.order_oid,
            ctx=ctx,
        )

    # 快照導出檔名帶模型（非法字元清洗同 _export_sheet_title；口徑細節在 xlsx「歸因統計」A2）
    safe_model = re.sub(r"[:\\/?*\[\]]", "", body.snapshot_model) if body.snapshot_model else ""
    snap_tag = f"_{safe_model}" if safe_model else ""
    filename = f"problems_{body.source or 'all'}{snap_tag}.xlsx"
    job_id = export_jobs.start_export(_builder, filename)
    return {"job_id": job_id, "filename": filename}


@router.get("/api/problems/attribution_overview")
def get_attribution_overview(
    source: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    granularity: str = "month",
    product_verticals: str | None = None,
    model: str | None = None,
    _user: dict = Depends(auth.get_current_user),
) -> dict:
    """歸因概覽聚合（概覽頁專用）：KPI + 傾向/L1域/信心分層/星等 分布 + 趨勢。

    可選 date_from/date_to（'YYYY-MM-DD' 區間，含端點）與 granularity（year|month|day，趨勢粒度）；
    product_verticals（逗號串，全局商品垂直分類篩選；僅 product_reviews 生效）；
    model（逗號串，判決模型多選——當前判決維度，僅套判決級指標，total_intake 不受影響）。
    """
    return db.attribution_overview(
        source=source,
        date_from=date_from,
        date_to=date_to,
        granularity=granularity,
        product_vertical=_csv_strs(product_verticals),
        model=_csv_strs(model),
    )


@router.get("/api/problems/attribution_breakdown")
def get_attribution_breakdown(
    l1: str,
    source: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    product_verticals: str | None = None,
    model: str | None = None,
    _user: dict = Depends(auth.get_current_user),
) -> dict:
    """某 L1 歸因域下的 L2/L3 細項分布（縱覽長條下鑽·懶載）；可選 date_from/date_to 區間 + 全局商品垂直分類 + 判決模型（CSV 多選）。"""
    return db.attribution_breakdown(
        source=source,
        l1=l1,
        date_from=date_from,
        date_to=date_to,
        product_vertical=_csv_strs(product_verticals),
        model=_csv_strs(model),
    )
