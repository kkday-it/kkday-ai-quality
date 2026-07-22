"""問題列表導出：美化 xlsx（1:N fan-out：每條歸因一列 + review 級欄合併儲存格）。

整列底色依 polarity（正綠/中灰/負紅）；行高顯式鎖定為「排除評論內容/商品名稱/方案名稱
長文欄」後各欄所需高度（長文欄超出截斷顯示、不撐爆列高）。資料表尾附 C-1~C-6 六域命中欄
（符合/不符合，供 Excel 篩選）；另附「分類統計」圖表工作表（本次導出的情緒傾向/L1/L2/
分層/階段/模型分佈，見 export_stats.py）與「Prompts」工作表（初判 prompt active 版本快照，
初判溯源）。
"""

from __future__ import annotations

import re
from datetime import timezone
from typing import TYPE_CHECKING

from app.core.db._shared import (
    _POLARITY_LABEL_ZH,
    _STAGE_LABEL_ZH,
    _STATUS_LABEL_ZH,
    _TIER_LABEL_ZH,
    _domain_owner,
    _summary_langs,
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
    ),  # rec_desc：評論正文（review 級，初判主輸入）；凍結邊界：前 4 欄（編號～評論內容）橫捲固定
    ("評論星等", "score", 8),
    ("評論時間", "occurred_at", 20),
    ("訂單號", "order_mid", 16),
    ("出發日", "go_date", 14),
    ("商品編號", "prod_oid", 12),
    ("商品名稱", "prod_name", 28),
    ("方案編號", "pkg_oid", 12),
    (
        "方案名稱",
        "package_name",
        28,
    ),  # order_snap_json 多語快照取 package_name（僅有訂單快照的來源有值）
    (
        "問題摘要",
        "summary",
        40,
    ),  # attr 級：LLM 繁中一句話概括（原 problem_summary，逐字佐證另存 evidence）
    ("情緒傾向", "our_sentiment", 10),  # 我方情緒分 1-5（正5/中3/負1；與外部評論同尺度）
    ("L1 分類", "l1_label", 14),
    ("L2 分類", "l2_label", 14),
    ("信心度", "confidence", 8),
    ("信心分層", "confidence_tier", 12),
    ("初判階段", "prejudge_stage", 12),
    ("初判模型", "model", 14),  # 初判溯源（attr 級；快照模式＝所選輸出版本模型）
    # 初判時間（review 級·合併）：該評論最新初判事件時間（attribution_history created_at，
    # _attach_prejudge_provenance 注入；未初判空白）
    ("初判時間", "prejudged_at", 20),
    # 該歸因所屬域 prompt 的「檔名 + 發版時間戳」（attr 級；初判當時快照 params.prompt_versions，
    # 舊紀錄缺快照→空白，重新初判即補）；如「03_C-3_supplier v20260716080435」
    ("Prompt 版本", "prompt_version", 32),
    # 極性 prompt 版本（review 級·合併）：情緒傾向欄的溯源，如「00_polarity v20260717030553」
    ("極性 Prompt 版本", "polarity_prompt_version", 28),
    # ── 判決組（判決軸：對初判結果的裁決；快照模式歷史切片無判決軸，三欄空白屬預期）──
    ("判決狀態", "status", 10),  # 待判決/自動確認/已確認/已駁回（attr 級）
    ("判決時間", "verdict_at", 20),  # attr 級；系統判決＝路由當下、人工判決＝操作當下
    ("判決人", "verdict_by", 20),  # 人工＝email；系統＝system:auto_confirm
]

# openpyxl 禁用的控制字元（\x00-\x08\x0b\x0c\x0e-\x1f）；源資料商品名/評論可能夾帶 → 寫 xlsx 前剔除
_XLSX_ILLEGAL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

# 資料列每行文字高度（pt）：Excel 預設字體（Calibri 11）單行列高
_LINE_HEIGHT_PT = 15


def _export_cell(key: str, value) -> str:
    """導出單格：時間欄正規化、傾向/分層/初判階段 code→繁中、情緒分數字化，其餘原樣。"""
    if value is None or value == "":
        return ""
    if key in ("occurred_at", "prejudged_at", "verdict_at"):
        return fmt_datetime(value)
    if key == "go_date":
        return fmt_datetime(value, date_only=True)
    if key == "polarity":
        return _POLARITY_LABEL_ZH.get(value, value)
    if key == "our_sentiment":
        return str(value)  # 我方情緒分 1-5 純數字，直接字串化
    if key == "confidence_tier":
        return _TIER_LABEL_ZH.get(value, value)
    if key == "prejudge_stage":
        return _STAGE_LABEL_ZH.get(value, value)
    if key == "status":
        return _STATUS_LABEL_ZH.get(value, value)
    return value


def _xlsx_safe(value):
    """xlsx 格值清洗：str 剔除 openpyxl 非法控制字元（否則 IllegalCharacterError）；非 str 原樣。"""
    return _XLSX_ILLEGAL_RE.sub("", value) if isinstance(value, str) else value


def _flat_attr(a: dict) -> dict:
    """歸因巢狀 DTO（attribution_dto）→ 導出用扁平欄（對齊 _EXPORT_XLSX_COLS 的 attr key）。"""
    return {
        "l1_label": (a.get("l1") or {}).get("label"),
        "l2_label": (a.get("l2") or {}).get("label"),
        "confidence": (a.get("confidence") or {}).get("value"),
        "confidence_tier": (a.get("confidence") or {}).get("tier"),
        "prejudge_stage": a.get("stage"),
        "summary": (a.get("content") or {}).get("summary"),
        "model": a.get("model"),
        "prompt_version": a.get(
            "prompt_version"
        ),  # _attach_prejudge_provenance 注入（無紀錄＝缺鍵→空白）
        "status": a.get("status"),
        "verdict_at": a.get("verdict_at"),
        "verdict_by": a.get("verdict_by"),
    }


def _compare_cols(models: list[str]) -> list[tuple[str, str, int]]:
    """並排對比模型 → 每模型一組 review 級欄（情緒/L1/L2）；欄鍵前綴 `cmp__{model}__*`。

    鍵前綴確保不與 attr 級鍵（_attr_keys）撞名 → fan-out 迴圈自動當 review 級處理（合併儲存格）。
    """
    cols: list[tuple[str, str, int]] = []
    for m in models:
        cols += [
            (f"情緒·{m}", f"cmp__{m}__sent", 8),
            (f"L1·{m}", f"cmp__{m}__l1", 14),
            (f"L2·{m}", f"cmp__{m}__l2", 14),
        ]
    return cols


def _compare_values(snap_attrs: list[dict]) -> tuple[str, str, str]:
    """某模型某評論的快照歸因陣列 → (情緒分, L1 labels、串接, L2 labels、串接)。

    情緒取 primary（或首條）sentiment_score；L1/L2 取 distinct label 保序串接。空陣列（該模型
    判為 non_issue 或未初判）→ 三欄皆空（前端/檔案以空白表達「該模型無歸因」）。
    """
    if not snap_attrs:
        return "", "", ""
    primary = next((a for a in snap_attrs if a.get("is_primary")), snap_attrs[0])
    sent = primary.get("sentiment_score")
    l1 = _join_labels((a.get("l1") or {}).get("label") for a in snap_attrs)
    l2 = _join_labels((a.get("l2") or {}).get("label") for a in snap_attrs)
    return (str(sent) if sent else ""), l1, l2


def _join_labels(labels) -> str:
    """label 迭代器 → distinct 保序「、」串接（去空/去重）。"""
    seen: dict[str, None] = {}
    for lb in labels:
        if lb and lb not in seen:
            seen[lb] = None
    return "、".join(seen)


def _adapt_snapshot(a: dict, model: str) -> dict:
    """attribution_history 快照單筆（snapshot_of 形狀）→ attribution_dto 輸出形狀（快照導出用）。

    - content.summary：快照存原始語系 map → 複用 `_summary_langs` 重算 {summary zh-tw 字串,
      summary_langs}，與當前初判導出完全同形。
    - owner：純函式 `_domain_owner(l1.code)` 讀取時派生（與 attribution_dto 同源）。
    - notes_count＝0：快照 finding_id 是歷史值，重新初判後與現行 finding_notes 的對應不可靠，
      且那些備註語義上屬「當時那次初判」——不 join、不冒充。
    - status＝None：人工判決軸綁「當前初判」可變狀態，歷史快照是不可變切片，
      硬塞會產生假象（xlsx 該欄輸出空白屬預期）。
    """
    content = a.get("content") or {}
    langs = _summary_langs(content.get("summary"))
    l1_code = (a.get("l1") or {}).get("code")
    return {
        **a,
        "content": {
            "summary": langs.get("zh-tw") or next(iter(langs.values()), None),
            "summary_langs": langs,
            "evidence": content.get("evidence"),
            "action": content.get("action"),
        },
        "owner": _domain_owner(l1_code or ""),
        "model": model,
        "notes_count": 0,
        "status": None,
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
    product_vertical: str | list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    sentiment: list[int] | None = None,
    stage: list[str] | None = None,
    confidence_tier: str | None = None,
    taxonomy: list[str] | None = None,
    status: list[str] | None = None,
    model: list[str] | None = None,
    snapshot_model: str | None = None,
    compare_models: list[str] | None = None,
    has_external: bool | None = None,
    rec_oid: str | None = None,
    prod_oid: str | None = None,
    order_oid: str | None = None,
    ctx: ExportCtx | None = None,
) -> bytes:
    """依篩選/選取導出統一問題列表為**美化 xlsx**（1:N fan-out：每條歸因一列，review 級欄合併）。

    複用 rule_export._style_header（品牌綠表頭/凍結首列/斑馬/細邊框），與規則導出視覺一致。
    傾向/分層/初判階段輸出繁中 label。openpyxl / _style_header lazy import。

    Args:
        source/polarity/judged/product_vertical/date_from/date_to: 同 list_problems 篩選（與畫面一致）。
        stage/confidence_tier/taxonomy/status/model/has_external/rec_oid/prod_oid/order_oid:
            同 list_problems，使導出＝列表所見即所得（全篩選對齊，非只部分）。
        snapshot_model: 輸出結果版本——None/空＝當前初判（現行為）；指定模型＝內容替換為該
            模型的 attribution_history 最新快照（真多模型對比輸出）。篩選仍依**當前初判**圈選
            評論（表級照常、初判級口徑落差以統計表附註揭露）；該模型未初判過的評論整列排除。
        compare_models: 並排對比模型（可複選）；每個模型在基準（gpt 當前初判或 snapshot_model）
            右側附一組 review 級欄「情緒·M / L1·M / L2·M」，值取該模型 attribution_history 最新快照。
            與 snapshot_model 語義獨立可並用（基準決定 fan-out 內容，compare 只加對比欄）。
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
        product_vertical=product_vertical,
        date_from=date_from,
        date_to=date_to,
        sentiment=sentiment,
        stage=stage,
        confidence_tier=confidence_tier,
        taxonomy=taxonomy,
        status=status,
        model=model,
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
    stats_note: str | None = None
    if snapshot_model:
        # 輸出結果版本＝指定模型：內容替換為該模型最新歷史快照（該模型未初判過的評論整列排除），
        # 並同步 row 級 polarity/our_sentiment——否則整列底色/情緒傾向欄仍是當前初判值，
        # 與被替換的 L1/L2/摘要（快照值）自相矛盾。
        from app.core.db.attribution_history import latest_snapshots

        snaps = latest_snapshots(source or "", snapshot_model)
        matched = [r for r in rows if r.get("_group") in snaps]
        stats_note = (
            f"輸出結果版本＝{snapshot_model}；篩選命中 {len(rows)} 則，"
            f"其中 {len(matched)} 則有該模型初判紀錄（已排除 {len(rows) - len(matched)} 則）"
        )
        rows = matched
        for r in rows:
            adapted = [
                _adapt_snapshot(a, snapshot_model) for a in snaps[r["_group"]]["attributions"]
            ]
            r["attributions"] = adapted
            primary = next(
                (a for a in adapted if a.get("is_primary")), adapted[0] if adapted else None
            )
            r["polarity"] = primary.get("polarity") if primary else None
            r["our_sentiment"] = primary.get("sentiment_score") if primary else None
    # 並排對比模型：每模型一組 review 級欄（情緒/L1/L2）附在基準右側；值取該模型最新快照，
    # 逐 row 注入 `cmp__{model}__*` 鍵——鍵前綴不撞 _attr_keys，故 fan-out 迴圈自動當 review
    # 級處理（合併儲存格、參與行高），無須改動渲染主迴圈。
    cmp_cols: list[tuple[str, str, int]] = []
    if compare_models:
        from app.core.db.attribution_history import latest_snapshots

        cmp_cols = _compare_cols(compare_models)
        snaps_by_model = {m: latest_snapshots(source or "", m) for m in compare_models}
        for r in rows:
            for m in compare_models:
                snap = snaps_by_model[m].get(r["_group"])
                sent, l1, l2 = _compare_values(snap["attributions"] if snap else [])
                r[f"cmp__{m}__sent"], r[f"cmp__{m}__l1"], r[f"cmp__{m}__l2"] = sent, l1, l2
        cmp_note = "並排對比模型（值＝各模型 attribution_history 最新快照）：" + "、".join(
            compare_models
        )
        stats_note = f"{stats_note}；{cmp_note}" if stats_note else cmp_note
    # C-1~C-6 六域命中欄（review 級·合併儲存格）：值＝符合/不符合（未初判評論空白），供 Excel
    # 篩選。以基準內容計（當前初判或 snapshot_model 快照）——置於快照替換後，輸出版本口徑一致；
    # 欄鍵 dom__{域機器值} 不撞 _attr_keys → fan-out 迴圈自動當 review 級處理。
    # 初判溯源注入：初判時間（review）＋域/極性 prompt 版本（attribution_history 快照 params）
    _attach_prejudge_provenance(rows, source)
    dom_cols = _domain_match_cols()
    for r in rows:
        hits = {(a.get("l1") or {}).get("code") for a in (r.get("attributions") or [])}
        judged = bool(
            r.get("polarity")
        )  # polarity 空＝完全未初判 → 六欄留空（避免誤讀為判過不符合）
        for _t, key, _w in dom_cols:
            r[key] = ("符合" if key.removeprefix("dom__") in hits else "不符合") if judged else ""
    total = len(rows)
    if ctx is not None:
        ctx.report(0, total)  # 資料到手、開始組檔：告知前端總量（進度條由「準備中」轉實際百分比）
    cols = _EXPORT_XLSX_COLS + dom_cols + cmp_cols
    wb = Workbook()
    ws = wb.active
    ws.title = _export_sheet_title(source, rows, date_from, date_to)
    ws.append([c[0] for c in cols])
    # 歸因級欄（逐條歸因不同、不合併）：問題摘要＝各歸因自己的痛點片段，故留 attr 級。
    # ⚠️ 新增歸因級欄位必須同步三處：_EXPORT_XLSX_COLS + _flat_attr + 本集合——缺此集合會
    # fallback 讀 row 級（_enrich_problem 的 status 恆 None）→ 欄位靜默空白（status 曾踩）。
    _attr_keys = {
        "l1_label",
        "l2_label",
        "confidence",
        "confidence_tier",
        "prejudge_stage",
        "summary",
        "model",
        "prompt_version",
        "status",
        "verdict_at",
        "verdict_by",
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
    # polarity 整列底色（正綠/中灰/負紅；未初判不上色）。置於「合併前」——此時全為普通 cell，
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
    # 顯式行高＝排除長文欄後各欄所需換行行數之最大值：超長的評論內容/商品名稱不再把整列
    # 撐爆，其餘欄位仍完整可見（wrap_text 下 Excel 只對「未設高」的列 auto-fit，設高即鎖定）。
    # review 級合併欄的值在合併首列（sr），其所需行數平攤到 n 列。
    _height_exempt = {
        "content",
        "prod_name",
        "package_name",
    }  # 長文欄：不參與行高計算，超出交給截斷顯示
    for sr, n in merges:
        base = 1  # review 級欄（合併區塊整體）平攤後的每列行數
        for ci in review_col_idx:
            _t, key, w = cols[ci - 1]
            if key in _height_exempt:
                continue
            need = -(-_wrapped_lines(ws.cell(row=sr, column=ci).value, w) // n)  # ceil
            base = max(base, need)
        for rr in range(sr, sr + n):
            lines = base
            for ci, (_t, key, w) in enumerate(cols, start=1):
                if key in _attr_keys:  # 歸因級欄逐列有值、不合併
                    lines = max(lines, _wrapped_lines(ws.cell(row=rr, column=ci).value, w))
            ws.row_dimensions[rr].height = lines * _LINE_HEIGHT_PT
    # 緊接資料表後附「分類統計」圖表工作表（本次導出資料的情緒傾向/L1/L2/分層/階段/模型分佈；
    # 所見即所得——快照模式下 rows 已替換為所選模型內容，統計自動跟隨；note 揭露輸出版本口徑）
    from app.core.db.export_stats import append_stats_sheet

    append_stats_sheet(wb, rows, note=stats_note)
    # 尾附「Prompts」工作表：初判 prompt active 版本快照，供事後追溯這份結果用哪版 prompt 產出
    _append_prompts_sheet(wb)
    # 最後附「說明」工作表：欄位語義字典（檔案轉發他人時自解釋）
    _append_legend_sheet(wb, bool(cmp_cols))
    if ctx is not None:
        ctx.report(total, total)  # 組檔完成（save 為單次序列化，無法再細分進度）
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _wrapped_lines(value, col_width: int) -> int:
    """估算儲存格值在指定欄寬（Excel 字元單位）wrap 後的顯示行數。

    欄寬單位≈半形字元數；CJK/全形字以 2 計（east_asian_width W/F）。逐 \\n 段落
    各自 ceil(顯示寬/可用寬) 後加總。估算值供顯式行高用，允許 ±1 行誤差。

    Args:
        value: 儲存格值（None/數字/字串皆可，內部字串化）。
        col_width: 該欄欄寬（_EXPORT_XLSX_COLS 第三元素）。

    Returns:
        至少 1 的行數估計。
    """
    import unicodedata

    if value is None or value == "":
        return 1
    usable = max(col_width - 1, 1)  # 扣約 1 字元 cell 內距
    lines = 0
    for seg in str(value).split("\n"):
        width = sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in seg)
        lines += max(1, -(-width // usable))  # ceil
    return lines


def _domain_match_cols() -> list[tuple[str, str, int]]:
    """C-1~C-6 六域命中欄定義（標題, 欄鍵, 欄寬）：標題如「C-1 商品內容」。

    欄序/域機器值/中文 label 皆取 `prompt_source.structure()`（六域結構 SSOT，隨 prompt 改動
    自動跟隨，程式碼零 taxonomy 假設）；C-N 碼由 prompt_id（如 01_C-1_content）派生。
    """
    from app.judge import prompt_source

    domains = prompt_source.structure()["domains"]
    cols: list[tuple[str, str, int]] = []
    for pid, d in zip(prompt_source.DOMAIN_PROMPT_IDS, domains, strict=True):
        cn = pid.split("_")[1]  # "01_C-1_content" → "C-1"
        cols.append((f"{cn} {d['domain_label']}", f"dom__{d['domain']}", 13))
    return cols


def _append_prompts_sheet(wb) -> None:
    """附「Prompts」工作表：導出當下 7 支初判 prompt 的 active 版本快照（初判溯源）。

    版本 meta 取 `judge_rule_versions` active 版（`db.list_rule_meta`）；版本欄顯示**發版時間戳**
    （v20260717031507 形式，UTC）——七支通常同批發版、時間戳一致可讀，per-rule 整數流水號
    （v19 之類）各支不齊、對閱讀者無意義，不輸出。內容全文 DB active 優先，無 DB 版
    （如全新環境）回退 `prompts/*.md` 檔案默認並於版本欄標「檔案默認」。
    內容逾 Excel 單格 32767 字元上限時截斷並標註（現行 prompt 最大約 2 萬字元，屬防禦）。
    """
    from app.core import db, paths
    from app.core.judge_config.rule_export import _style_header
    from app.judge import prompt_source

    _CELL_MAX = 32000  # Excel 單格字元上限 32767，留緩衝
    _CONTENT_ROW_PT = 120  # 內容列固定高（約 8 行預覽；全文點入儲存格檢視）
    meta = {m["rule_code"]: m for m in db.list_rule_meta()}
    ws = wb.create_sheet("Prompts")
    cols = [
        ("Prompt", 16),
        ("名稱", 18),
        ("版本", 10),
        ("版本說明", 28),
        ("發版時間", 20),
        ("內容全文", 100),
    ]
    ws.append([t for t, _w in cols])
    for pid, code in zip(prompt_source.PROMPT_IDS, prompt_source.PROMPT_RULE_CODES, strict=True):
        m = meta.get(code) or {}
        active = db.get_rule_active(code)
        if active and isinstance(active.get("text"), str) and active["text"].strip():
            text = active["text"]
        else:  # 無 DB active 版（全新環境）→ 檔案默認
            text = (paths.PROMPTS_DIR / f"{pid}.md").read_text(encoding="utf-8")
        if len(text) > _CELL_MAX:
            text = text[:_CELL_MAX] + "\n…（逾 Excel 單格上限，全文見系統「規則配置」）"
        title = m.get("label") or prompt_source.load(pid).get("title") or pid
        # 版本＝發版時間戳（UTC；judge_rule_versions.created_at 為 timestamptz datetime）
        created = m.get("created_at")
        version = f"v{created.astimezone(timezone.utc):%Y%m%d%H%M%S}" if created else "檔案默認"
        ws.append(
            [
                pid,  # prompt 檔名 id（與資料表「Prompt 版本」值同詞彙，直接對照）
                _xlsx_safe(title),
                version,
                _xlsx_safe(m.get("note") or ""),
                fmt_datetime(m.get("created_at")) if m.get("created_at") else "",
                _xlsx_safe(text),
            ]
        )
    _style_header(ws, [w for _t, w in cols])  # 已含全表 wrap+頂對齊
    for rr in range(2, ws.max_row + 1):  # 內容列固定高：全文預覽約 8 行，不撐爆版面
        ws.row_dimensions[rr].height = _CONTENT_ROW_PT


def _attach_prejudge_provenance(rows: list[dict], source: str | None) -> None:
    """就地注入初判溯源三件套：review 級 `prejudged_at`/`polarity_prompt_version`、
    attr 級 `prompt_version`。

    來源＝attribution_history 每評論最新初判快照：`created_at`＝初判事件落庫時間（初判時間；
    f2a8c4d61e93 已回填故全部已初判評論有值）；`params.prompt_versions`（初判落庫時的 7 支
    版本快照，見 prejudge_batch._resolve_versions_used）換算為「prompt 檔名 + 發版時間戳」
    （如「03_C-3_supplier v20260716080435」，與「Prompts」工作表同詞彙可直接對照）。
    舊紀錄缺版本快照 → 版本欄空白（重新初判即補）；快照/當前兩種輸出版本皆以各歸因
    自身 model 對應的最新快照為準。
    """
    from sqlalchemy import select

    from app.core.db import tables as T
    from app.core.db.attribution_history import latest_snapshots
    from app.judge import prompt_source

    models = {a.get("model") for r in rows for a in (r.get("attributions") or []) if a.get("model")}
    if not models:
        return
    # rule_code → prompt 檔名 id（值前綴，讀者可直接對照「Prompts」工作表）
    rule_pid = dict(zip(prompt_source.PROMPT_RULE_CODES, prompt_source.PROMPT_IDS, strict=True))
    # (rule_code, 整數版本) → 「pid v發版時間戳」；一次撈全表（prompt_* 版本列僅百級）
    j = T.judge_rule_versions
    with T.get_engine().connect() as c:
        stamp = {
            (r.rule_code, r.version): rule_pid.get(r.rule_code, r.rule_code)
            + " "
            + (
                f"v{r.created_at.astimezone(timezone.utc):%Y%m%d%H%M%S}"
                if r.created_at
                else f"v{r.version}"
            )
            for r in c.execute(
                select(j.c.rule_code, j.c.version, j.c.created_at).where(
                    j.c.rule_code.like("prompt\\_%")
                )
            )
        }

    def _stamp_of(code: str, vers: dict) -> str | None:
        """版本快照 dict → 該 rule 的「pid v時間戳」；無紀錄回 None。"""
        ver = vers.get(code)
        if ver is None:
            return None
        return stamp.get((code, ver), f"{rule_pid.get(code, code)} v{ver}")

    # 域機器值（attributions.l1_code 詞彙表）→ rule_code（prompt_C-N）
    dom_rule = {
        pid.split("_", 2)[2]: code
        for pid, code in zip(prompt_source.PROMPT_IDS, prompt_source.PROMPT_RULE_CODES, strict=True)
        if pid != prompt_source.POLARITY_ID
    }
    polarity_rule = prompt_source.PROMPT_RULE_CODES[
        prompt_source.PROMPT_IDS.index(prompt_source.POLARITY_ID)
    ]
    versions_by_model = {m: latest_snapshots(source or "", m) for m in models}
    for r in rows:
        attrs = r.get("attributions") or []
        if not attrs:
            continue
        # 同一評論全部歸因同 model：以首條 model 取該評論最新初判事件
        snap = versions_by_model.get(attrs[0].get("model"), {}).get(r.get("_group"))
        if snap:
            r["prejudged_at"] = snap.get("created_at") or ""  # 初判時間（review 級）
        vers = (snap or {}).get("params", {}).get("prompt_versions") or {}
        pol = _stamp_of(polarity_rule, vers)
        if pol:
            r["polarity_prompt_version"] = pol  # 極性 prompt 溯源（review 級）
        for a in attrs:
            code = dom_rule.get((a.get("l1") or {}).get("code") or "")
            v = _stamp_of(code, vers) if code else None
            if v:
                a["prompt_version"] = v


def _append_legend_sheet(wb, has_compare: bool) -> None:
    """附「說明」工作表：欄位語義字典——檔案轉發給未接觸系統的人也能自解釋。

    內容與資料表欄位定義（_EXPORT_XLSX_COLS/_domain_match_cols）同步維護；新增/改欄時
    一併更新本表條目（docs-sync 鐵律的檔內對應物）。
    """
    from app.core.judge_config.rule_export import _style_header

    ws = wb.create_sheet("說明")
    ws.append(["項目", "說明"])
    rows = [
        (
            "兩階段模型",
            "初判分類（AI 管線：極性閘門→六域並行歸因→信心閘門）產出候選歸因；判決歸因（判決軸）對初判結果裁決——系統判決（高信心自動確認）或人工判決（確認/駁回）",
        ),
        (
            "工作表結構",
            "①資料表（每列＝一條歸因；同評論多歸因時評論級欄合併儲存格）②分類統計（本次導出的分佈圖表）③Prompts（導出當下 7 支初判 prompt 的 active 版本快照）④本說明",
        ),
        ("整列底色", "依評論情緒傾向：正向＝淡綠、中立＝淡灰、負向＝淡紅；未初判不上色"),
        (
            "評論級 vs 歸因級",
            "編號～方案名稱、情緒傾向、初判時間、極性 Prompt 版本、C-1~C-6 為評論級（多歸因時合併儲存格）；問題摘要、L1/L2、信心度、信心分層、初判階段、初判模型、Prompt 版本、判決狀態/時間/人為歸因級（逐列各自有值）",
        ),
        (
            "情緒傾向",
            "我方 LLM 讀評論原文判定的情緒分 1-5（負 1-2／中 3／正 4-5），與外部評論 sentiment 同尺度",
        ),
        ("信心分層", "初判信心三層：自動採信／評審複審／人工複審（閾值見系統設定）"),
        ("初判階段", "AI 初判完成度：已初判／待複審／待數據補充；空白＝尚未初判"),
        ("初判時間", "該評論最近一次初判事件的落庫時間；空白＝尚未初判"),
        (
            "Prompt 版本",
            "該條歸因所屬「域」prompt 的檔名＋發版時間戳（初判當時所用版本，非導出當下）；空白＝該筆初判早於版本快照機制，重新初判即補",
        ),
        ("極性 Prompt 版本", "情緒傾向所用 00_polarity prompt 的版本（語義同上）"),
        ("判決狀態", "判決軸：待判決／自動確認（系統判決）／已確認／已駁回（人工判決）"),
        (
            "判決時間/判決人",
            "該歸因被裁決的時間與主體：系統判決＝system:auto_confirm＋路由當下；人工判決＝操作者 email＋操作當下；待判決＝空白",
        ),
        (
            "C-1~C-6 六域欄",
            "該評論是否命中該歸因域：符合／不符合；空白＝尚未初判。可用表頭篩選箭頭快速過濾",
        ),
        (
            "兩個版本語義",
            "資料表「Prompt 版本」＝該列初判當時所用版本；「Prompts」工作表＝導出當下系統 active 版本——兩者不一致代表該列由較舊版本初判",
        ),
    ]
    if has_compare:
        rows.append(
            (
                "對比模型欄",
                "情緒·M／L1·M／L2·M＝該模型最新初判快照的並排對比（空白＝該模型未初判或判為無問題；歷史快照無判決軸）",
            )
        )
    for item, desc in rows:
        ws.append([item, desc])
    _style_header(ws, [22, 110])  # 已含全表 wrap+頂對齊
