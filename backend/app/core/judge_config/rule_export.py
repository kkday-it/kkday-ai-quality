"""判決規則 → Excel 匯出（規則配置頁「導出 Excel」）。

讀 6 支域 prompt 的分類結構（prompt_source.structure()，每域一分頁，L2 面向代碼/名稱逐列——完整判準
文字〔界線、正反例〕已改為自由文本存在各域 prompt 的 System 區塊，本表僅列結構性面向清單，供 QC/PM
快速核對域/面向涵蓋範圍；完整判準請至規則配置頁「初判 Prompt」查看對應域 md）＋ global_rule 判決總規範
（額外一分頁，區塊/項目/內容 扁平呈現）。openpyxl 為重庫，於函式內 lazy import。
"""

from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.export_jobs import ExportCtx

# 域分頁欄位（結構源＝prompt_source.structure()，非 L1-L3 判準樹——canon/allow/forbid 已內嵌各域
# prompt System 自由文本，不再結構化，故僅列面向代碼/名稱）。
_DOMAIN_HEADERS = ["L2 面向代碼", "L2 面向名稱"]
_DOMAIN_WIDTHS = [16, 36]

# global 判決總規範分頁欄位（結構非 L1/L2/L3 樹，改以區塊/項目/內容 扁平呈現）。
_GLOBAL_HEADERS = ["區塊", "項目", "內容"]
_GLOBAL_WIDTHS = [22, 24, 80]


def _fmt(v: object) -> str:
    """global 內容格式化：dict/list 以縮排 JSON 呈現（保留結構可讀），純量轉字串。"""
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, indent=2)
    return "" if v is None else str(v)


def _style_header(ws, widths: list[int], freeze_cols: int = 0) -> None:
    """統一導出美化樣式：表頭品牌綠底白字 ＋ 凍結首列(+前 freeze_cols code 欄) ＋ 篩選箭頭
    ＋ 全表細邊框 ＋ 資料列斑馬紋 ＋ 欄寬 ＋ 內容自動換行頂對齊。所有導出共用此 helper 確保視覺一致。"""
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    thin = Side(style="thin", color="E5E6EB")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    head_fill = PatternFill("solid", fgColor="2E7D5B")  # 品牌綠表頭
    zebra = PatternFill("solid", fgColor="F7F8FA")  # 偶數資料列淡底（斑馬紋）

    for c in ws[1]:  # 表頭
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = head_fill
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = border
    ws.row_dimensions[1].height = 24

    # 凍結首列 + 前 freeze_cols 欄（code 欄橫捲時固定）；表頭加篩選箭頭
    # get_column_letter：欄數可能 > 26（多模型並排導出），naive chr(64+i) 會在第 27 欄溢出成 '['
    from openpyxl.utils import get_column_letter

    ws.freeze_panes = f"{get_column_letter(freeze_cols + 1)}2"
    ws.auto_filter.ref = ws.dimensions

    # 欄寬下限＝表頭標題一行所需寬（CJK 以 2 計）＋篩選箭頭佔位，保證標題不換行
    import unicodedata

    for i, w in enumerate(widths, 1):
        head = str(ws.cell(row=1, column=i).value or "")
        head_w = sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in head)
        ws.column_dimensions[get_column_letter(i)].width = max(w, head_w + 3)

    for r, row in enumerate(ws.iter_rows(min_row=2), start=2):  # 資料列
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
            c.border = border
            if r % 2 == 0:
                c.fill = zebra


def build_rules_workbook_bytes(ctx: ExportCtx | None = None) -> bytes:
    """組出判決規則 Excel（bytes）：全部 C-N 歸因分類各一分頁 ＋ global 判決總規範分頁。

    資料源＝DB active 版本（規則配置頁的當前生效內容，反映使用者最新編輯）。僅導出人閱分類法典
    （C-N + global）；schema 結構規格、product_vertical 選項池、judgment 判決配置（信心閾值/
    prejudge 旋鈕）皆非人閱法典，故不含（迴圈只走 C- 開頭 + global_rule 而自然排除）。

    Args:
        ctx: 背景 job 進度把手（可選）；給定時每完成一分頁回報進度並輪詢取消，None＝同步直呼。

    Returns:
        xlsx 檔的位元組內容（供 API 以 attachment 回傳）。

    Raises:
        Cancelled: ctx 對應 job 被取消時由 ctx.check() 拋出。
    """
    from openpyxl import Workbook

    from app.core import db
    from app.judge import prompt_source

    wb = Workbook()
    wb.remove(wb.active)  # 移除預設空表

    domains = prompt_source.structure()["domains"]
    total = len(domains) + 1  # 6 域各一分頁 + global 一分頁
    done = 0
    if ctx is not None:
        ctx.report(0, total)

    # 6 域面向清單（依 prompt_source.structure() 顯示序，即 C-1~C-6）；分頁名＝「rule_code label」。
    for i, d in enumerate(domains, start=1):
        if ctx is not None:
            ctx.check()
        code = f"C-{i}"
        title = f"{code} {d.get('domain_label', '')}".strip()[:31]  # 分頁名上限 31 字
        ws = wb.create_sheet(title)
        ws.append(_DOMAIN_HEADERS)
        for f in d.get("facets") or []:
            ws.append([f.get("code", ""), f.get("label", "")])
        _style_header(ws, _DOMAIN_WIDTHS, freeze_cols=1)  # 凍結面向代碼欄
        done += 1
        if ctx is not None:
            ctx.report(done, total)

    # global 判決總規範（區塊/項目/內容 扁平化；跳過 $schema/_meta 中繼欄）
    if ctx is not None:
        ctx.check()
    gcontent = db.get_rule_active("global_rule")
    if gcontent:
        grows: list[list[str]] = []
        for k, v in gcontent.items():
            if k in ("$schema", "_meta"):
                continue
            if isinstance(v, dict):
                for sk, sv in v.items():
                    grows.append([k, sk, _fmt(sv)])
            else:
                grows.append([k, "", _fmt(v)])
        gws = wb.create_sheet("global 判決總規範")
        gws.append(_GLOBAL_HEADERS)
        for r in grows:
            gws.append(r)
        _style_header(gws, _GLOBAL_WIDTHS, freeze_cols=1)  # 凍結「區塊」欄

    if ctx is not None:
        ctx.report(total, total)  # 全部分頁完成（save 為單次序列化，無法再細分）
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
