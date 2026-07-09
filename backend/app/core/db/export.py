"""問題列表導出：美化 xlsx（1:N fan-out：每條歸因一列 + review 級欄合併儲存格）。

整列底色依 polarity（正綠/中灰/負紅）；另附「歸因統計」圖表工作表
（本次導出的情緒傾向/L1/L2/分層/階段分佈，見 export_stats.py）。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from app.core.db._shared import (
    _POLARITY_LABEL_ZH,
    _STAGE_LABEL_ZH,
    _TIER_LABEL_ZH,
    fmt_datetime,
)
from app.core.db.problems import list_problems

if TYPE_CHECKING:
    from app.core.export_jobs import ExportCtx

# 每寫入多少 review 檢查一次取消旗標並回報進度（過密徒增鎖競爭、過疏取消不即時）。
_PROGRESS_STEP = 200

# 導出 xlsx 欄位（標題, 記錄鍵, 欄寬）：評論身份欄（編號～評論時間）前置並凍結；1:N 每條歸因一列（review 級欄合併）
_EXPORT_XLSX_COLS: list[tuple[str, str, int]] = [
    ("編號", "source_id", 14),
    ("來源", "source_label", 12),
    ("評論標題", "title", 28),  # rec_title：評論標題（review 級）
    (
        "評論內容",
        "content",
        48,
    ),  # rec_desc：評論正文（review 級，判決主輸入）；凍結邊界：前 4 欄（編號～評論內容）橫捲固定
    ("評論星等", "score", 8),
    ("評論時間", "occurred_at", 20),
    ("外部評論情緒傾向", "ext_sentiment", 12),  # 外部評論系統情緒分 1-5（僅商品評論來源有值）
    ("外部評論 Free Tag", "ext_free_tag", 40),  # 外部評論面向標籤摘要（每面向一行）
    ("訂單號", "order_mid", 16),
    ("出發日", "go_date", 14),
    ("商品編號", "prod_oid", 12),
    ("商品名稱", "prod_name", 28),
    (
        "問題摘要",
        "summary",
        40,
    ),  # attr 級：LLM 繁中一句話概括（原 problem_summary，逐字佐證另存 evidence）
    ("情緒傾向", "our_sentiment", 10),  # 我方情緒分 1-5（正5/中3/負1；與外部評論同尺度）
    ("L1 分類", "l1_label", 14),
    ("L2 分類", "l2_label", 14),
    ("L3 分類", "l3_label", 18),
    ("信心度", "confidence", 8),
    ("判決分層", "confidence_tier", 12),
    ("判決階段", "judgment_stage", 12),
]

# openpyxl 禁用的控制字元（\x00-\x08\x0b\x0c\x0e-\x1f）；源資料商品名/評論可能夾帶 → 寫 xlsx 前剔除
_XLSX_ILLEGAL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _export_cell(key: str, value) -> str:
    """導出單格：時間欄正規化、傾向/分層/判決階段 code→繁中、情緒分數字化、外部 free_tag 摘要化，其餘原樣。"""
    if key == "ext_free_tag":  # list[dict] → 多行摘要（空 list 亦回空字串，須先於下方 falsy 判斷）
        from app.core.db.comparison import ext_free_tag_summary

        return ext_free_tag_summary(value)
    if value is None or value == "":
        return ""
    if key == "occurred_at":
        return fmt_datetime(value)
    if key == "go_date":
        return fmt_datetime(value, date_only=True)
    if key == "polarity":
        return _POLARITY_LABEL_ZH.get(value, value)
    if key == "our_sentiment":
        return str(value)  # 我方情緒分 1-5 純數字，直接字串化
    if key == "confidence_tier":
        return _TIER_LABEL_ZH.get(value, value)
    if key == "judgment_stage":
        return _STAGE_LABEL_ZH.get(value, value)
    return value


def _xlsx_safe(value):
    """xlsx 格值清洗：str 剔除 openpyxl 非法控制字元（否則 IllegalCharacterError）；非 str 原樣。"""
    return _XLSX_ILLEGAL_RE.sub("", value) if isinstance(value, str) else value


def _flat_attr(a: dict) -> dict:
    """歸因巢狀 DTO（attribution_dto）→ 導出用扁平欄（對齊 _EXPORT_XLSX_COLS 的 attr key）。"""
    return {
        "l1_label": (a.get("l1") or {}).get("label"),
        "l2_label": (a.get("l2") or {}).get("label"),
        "l3_label": (a.get("l3") or {}).get("label"),
        "confidence": (a.get("confidence") or {}).get("value"),
        "confidence_tier": (a.get("confidence") or {}).get("tier"),
        "judgment_stage": a.get("stage"),
        "summary": (a.get("content") or {}).get("summary"),
    }


def _export_sheet_title(
    source: str | None, rows: list[dict], date_from: str | None, date_to: str | None
) -> str:
    """工作表名＝來源 label + 時間區間（如「商品評論 20260601~20260701」）。

    時間區間優先取日期篩選 date_from/date_to；未篩選則由匯出資料的 occurred_at 最小/最大值推導。
    Excel 工作表名限制：≤31 字、禁用 : \\ / ? * [ ]（超限/含禁字元會存檔失敗 → 清洗截斷）。
    """
    from app.core import sources as _sources

    label = _sources.label_for(source) if source else "全部來源"

    def _compact(s) -> str:
        """時間字串取前 8 位數字（YYYYMMDD）；無效回空。"""
        d = re.sub(r"\D", "", str(s or ""))
        return d[:8] if len(d) >= 8 else ""

    d1, d2 = _compact(date_from), _compact(date_to)
    if not (d1 and d2):  # 無日期篩選 → 由資料 occurred_at 推區間
        occ = sorted(o for o in (_compact(r.get("occurred_at")) for r in rows) if o)
        if occ:
            d1, d2 = d1 or occ[0], d2 or occ[-1]
    title = f"{label} {d1}~{d2}" if (d1 and d2) else label
    return re.sub(r"[:\\/?*\[\]]", "", title)[:31]


def export_problems_xlsx(
    source: str | None = None,
    polarity: str | list[str] | None = None,
    judged: bool | None = None,
    item_ids: list[str] | None = None,
    score: list[int] | None = None,
    product_vertical: str | list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    sentiment: list[int] | None = None,
    stage: list[str] | None = None,
    confidence_tier: str | None = None,
    l1_domain: str | None = None,
    has_external: bool | None = None,
    rec_oid: str | None = None,
    prod_oid: str | None = None,
    order_oid: str | None = None,
    ctx: ExportCtx | None = None,
) -> bytes:
    """依篩選/選取導出統一問題列表為**美化 xlsx**（1:N fan-out：每條歸因一列，review 級欄合併）。

    複用 rule_export._style_header（品牌綠表頭/凍結首列/斑馬/細邊框），與規則導出視覺一致。
    傾向/分層/判決階段輸出繁中 label。openpyxl / _style_header lazy import。

    Args:
        source/polarity/judged/score/product_vertical/date_from/date_to: 同 list_problems 篩選（與畫面一致）。
        stage/confidence_tier/l1_domain/has_external/rec_oid/prod_oid/order_oid: 同 list_problems，
            使導出＝列表所見即所得（全篩選對齊，非只部分）。
        item_ids: 給定時只導這些 review（前端勾選）；比對 fan-out 列的 _group（source_id）。
        ctx: 背景 job 進度把手（可選）；給定時逐 review 回報進度並輪詢取消（背景導出用），
            None＝同步直呼（測試 / 腳本）。

    Returns:
        xlsx 位元組（供 API 以 attachment 回傳）。

    Raises:
        Cancelled: ctx 對應 job 被取消時由 ctx.check() 拋出（背景 job 據此標 cancelled）。
    """
    from io import BytesIO

    from openpyxl import Workbook
    from openpyxl.styles import PatternFill

    from app.core.judge_config.rule_export import _style_header

    data = list_problems(
        source=source,
        polarity=polarity,
        judged=judged,
        score=score,
        product_vertical=product_vertical,
        date_from=date_from,
        date_to=date_to,
        sentiment=sentiment,
        stage=stage,
        confidence_tier=confidence_tier,
        l1_domain=l1_domain,
        has_external=has_external,
        rec_oid=rec_oid,
        prod_oid=prod_oid,
        order_oid=order_oid,
        limit=10_000_000,
    )
    rows = data["rows"]
    if item_ids:
        idset = set(item_ids)
        rows = [r for r in rows if r.get("_group") in idset]
    total = len(rows)
    if ctx is not None:
        ctx.report(0, total)  # 資料到手、開始組檔：告知前端總量（進度條由「準備中」轉實際百分比）
    cols = _EXPORT_XLSX_COLS
    wb = Workbook()
    ws = wb.active
    ws.title = _export_sheet_title(source, rows, date_from, date_to)
    ws.append([c[0] for c in cols])
    # 歸因級欄（逐條歸因不同、不合併）：問題摘要＝各歸因自己的痛點片段，故留 attr 級
    _attr_keys = {
        "l1_label",
        "l2_label",
        "l3_label",
        "confidence",
        "confidence_tier",
        "judgment_stage",
        "summary",
    }
    review_col_idx = [ci for ci, (_t, key, _w) in enumerate(cols, start=1) if key not in _attr_keys]
    merges: list[tuple[int, int]] = []  # (起始 Excel 列, 該 review 歸因數 N)
    r_excel = 2  # 資料起始列（表頭列 1）
    for ri, r in enumerate(rows):
        # 每 _PROGRESS_STEP 筆回報進度並檢查取消（取消時 ctx.check 拋 Cancelled 中止組檔）
        if ctx is not None and ri % _PROGRESS_STEP == 0:
            ctx.check()
            ctx.report(ri, total)
        attrs = r.get("attributions") or []
        n = max(1, len(attrs))
        for j in range(n):
            a = _flat_attr(attrs[j]) if j < len(attrs) else {}
            line = []
            for _title, key, _w in cols:
                src_val = a.get(key, "") if key in _attr_keys else r.get(key, "")
                line.append(_xlsx_safe(_export_cell(key, src_val)))
            ws.append(line)
        merges.append((r_excel, n))
        r_excel += n
    _style_header(ws, [c[2] for c in cols], freeze_cols=4)  # 凍結表頭 + 前 4 欄（編號～評論內容）
    # polarity 整列底色（正綠/中灰/負紅；傾向不明不上色）。置於「合併前」——此時全為普通 cell，
    # 可安全逐格設 fill（合併後 MergedCell 無法設樣式）；且晚於 _style_header 故覆蓋其斑馬紋。
    _pol_fill = {
        "positive": PatternFill("solid", fgColor="DCF3E3"),  # 正向：淡綠
        "neutral": PatternFill("solid", fgColor="EAEBEE"),  # 中立：淡灰
        "negative": PatternFill("solid", fgColor="FDE0E0"),  # 負向：淡紅
    }
    for (sr, n), r in zip(merges, rows, strict=True):
        fill = _pol_fill.get(r.get("polarity"))
        if fill is None:
            continue
        for rr in range(sr, sr + n):
            for cell in ws[rr]:
                cell.fill = fill
    # style + 上色後再合併同一 review 的 review 級欄（避免 MergedCell 樣式設定問題）
    for sr, n in merges:
        if n > 1:
            for ci in review_col_idx:
                ws.merge_cells(start_row=sr, start_column=ci, end_row=sr + n - 1, end_column=ci)
    # 緊接資料表後附「歸因統計」圖表工作表（本次導出資料的情緒傾向/L1/L2/分層/階段分佈；所見即所得）
    from app.core.db.export_stats import append_stats_sheet

    append_stats_sheet(wb, rows)
    if ctx is not None:
        ctx.report(total, total)  # 組檔完成（save 為單次序列化，無法再細分進度）
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
