"""判決規則 → Excel 匯出（規則配置頁「導出 Excel」）。

讀 DB active 版本的全部歸因分類（C-N，每域一分頁，葉判準逐列，欄位對齊
data/問題分類層級結構.xlsx / scripts/gen_taxonomy_xlsx.py）＋ global_rule 判決總規範
（額外一分頁，區塊/項目/內容 扁平呈現）。openpyxl 為重庫，於函式內 lazy import。
"""

from __future__ import annotations

import io
import json

# C-N 分頁欄位（對齊 scripts/gen_taxonomy_xlsx.py 的 HEADERS；離線腳本與線上導出維持同一導出格式）。
_TREE_HEADERS = [
    "L1 歸因域",
    "L2 面向／子因",
    "L3 細項",
    "法典條文 canon",
    "允許 allow",
    "禁止 forbid",
    "好範例 positive",
    "壞範例 negative",
]
_TREE_WIDTHS = [14, 16, 16, 44, 36, 36, 36, 36]

# global 判決總規範分頁欄位（結構非 L1/L2/L3 樹，改以區塊/項目/內容 扁平呈現）。
_GLOBAL_HEADERS = ["區塊", "項目", "內容"]
_GLOBAL_WIDTHS = [22, 24, 80]


def _cell(v: object) -> str:
    """陣列欄以換行併成單格（allow/forbid）；None→空字串。"""
    if isinstance(v, list):
        return "\n".join(str(x) for x in v)
    return "" if v is None else str(v)


def _fmt(v: object) -> str:
    """global 內容格式化：dict/list 以縮排 JSON 呈現（保留結構可讀），純量轉字串。"""
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, indent=2)
    return "" if v is None else str(v)


def _collect(node: dict, l1_label: str, l2_label: str, out: list[list[str]]) -> None:
    """遞迴走訪歸因樹，遇葉節點（無 children）輸出一列判準。

    支援變深度分類：葉可能落在 L2 或 L3——L3 欄僅 level≥3 的葉填 label，L2 葉留空、其 label 進 L2 欄。
    """
    label = node.get("label", "")
    level = node.get("level") or 0
    disp = f"{node.get('code', '')} {label}".strip()  # 各級 cell 帶 code（如「C-2-1 網路品質」）
    children = node.get("children", [])
    if level == 2:
        l2_label = disp
    if not children:  # 葉節點：輸出判準列
        l3 = disp if level >= 3 else ""
        out.append(
            [
                l1_label,
                l2_label,
                l3,
                node.get("canon", ""),
                _cell(node.get("allow")),
                _cell(node.get("forbid")),
                _cell(node.get("positive_cases")),
                _cell(node.get("negative_cases")),
            ]
        )
        return
    for child in children:
        _collect(child, l1_label, l2_label, out)


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
    ws.freeze_panes = f"{chr(65 + freeze_cols)}2"
    ws.auto_filter.ref = ws.dimensions

    for i, w in enumerate(widths, 1):
        ws.column_dimensions[chr(64 + i)].width = w

    for r, row in enumerate(ws.iter_rows(min_row=2), start=2):  # 資料列
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
            c.border = border
            if r % 2 == 0:
                c.fill = zebra


def build_rules_workbook_bytes() -> bytes:
    """組出判決規則 Excel（bytes）：全部 C-N 歸因分類各一分頁 ＋ global 判決總規範分頁。

    資料源＝DB active 版本（規則配置頁的當前生效內容，反映使用者最新編輯）。schema 結構規格與
    product_vertical 選項池非人閱分類法典，故不含。

    Returns:
        xlsx 檔的位元組內容（供 API 以 attachment 回傳）。
    """
    from openpyxl import Workbook

    from app.core import db

    wb = Workbook()
    wb.remove(wb.active)  # 移除預設空表

    # C-N 歸因分類（依 RULE_CODES 順序取 C- 開頭者）；分頁名＝「code label」對齊規則管理 UI 左選單。
    for code in [c for c in db.RULE_CODES if c.startswith("C-")]:
        content = db.get_rule_active(code)
        if not content or not content.get("tree"):
            continue
        l1 = content["tree"][0]
        # 域名優先取 _meta.label（＝規則管理 UI 左選單顯示名，SSOT），與使用者所見一致；缺則退樹根 label。
        l1_label = (content.get("_meta") or {}).get("label") or l1.get("label", "")
        rows: list[list[str]] = []
        _collect(l1, f"{code} {l1_label}".strip(), "", rows)  # L1 cell 帶 C-N code
        title = f"{code} {l1_label}".strip()[:31]  # 分頁名上限 31 字
        ws = wb.create_sheet(title)
        ws.append(_TREE_HEADERS)
        for r in rows:
            ws.append(r)
        _style_header(ws, _TREE_WIDTHS, freeze_cols=3)  # 凍結 L1/L2/L3 三 code 欄

    # global 判決總規範（區塊/項目/內容 扁平化；跳過 $schema/_meta 中繼欄）
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

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
