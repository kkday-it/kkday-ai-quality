"""判決規則 → Excel 匯出（規則配置頁「導出 Excel」）。

讀 DB active 版本的全部歸因分類（C-N，每域一分頁，各級判準逐列——L1 域／L2 面向帶判準者亦輸出界線列，欄位對齊
data/問題分類層級結構.xlsx / scripts/tools/gen_taxonomy_xlsx.py）＋ global_rule 判決總規範
（額外一分頁，區塊/項目/內容 扁平呈現）。openpyxl 為重庫，於函式內 lazy import。
"""

from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.export_jobs import ExportCtx

# C-N 分頁欄位（對齊 scripts/tools/gen_taxonomy_xlsx.py 的 HEADERS；離線腳本與線上導出維持同一導出格式）。
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
    """遞迴走訪歸因樹，輸出判準列：葉節點恆輸出；分支（L1 域／L2 面向）若自帶 canon 判準亦輸出一列。

    支援變深度分類：葉可能落在 L2 或 L3——L3 欄僅 level≥3 的葉填 label，L2（葉或帶判準分支）label 進 L2
    欄、L3 留空，L1 域列 L2/L3 皆空。分支帶判準（cascade 分層界線）者，先輸出自身域／面向界線列再遞迴子節點，
    使域→面向→細項的界線在導出中層層可見。
    """
    label = node.get("label", "")
    level = node.get("level") or 0
    disp = f"{node.get('code', '')} {label}".strip()  # 各級 cell 帶 code（如「C-2-1 網路品質」）
    children = node.get("children", [])
    if level == 2:
        l2_label = disp
    # 葉節點恆輸出；分支帶 canon（L1/L2 判準）亦輸出自身界線列（先於子節點，維持父→子順序）
    if not children or node.get("canon"):
        l3 = disp if level >= 3 else ""
        l2 = l2_label if level >= 2 else ""  # L1 域列的 L2 欄留空
        out.append(
            [
                l1_label,
                l2,
                l3,
                node.get("canon", ""),
                _cell(node.get("allow")),
                _cell(node.get("forbid")),
                _cell(node.get("positive_cases")),
                _cell(node.get("negative_cases")),
            ]
        )
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

    wb = Workbook()
    wb.remove(wb.active)  # 移除預設空表

    c_codes = [c for c in db.RULE_CODES if c.startswith("C-")]
    total = (
        len(c_codes) + 1
    )  # C-N 各一分頁 + global 一分頁（進度總量近似，跳過的空 code 亦計入步進）
    done = 0
    if ctx is not None:
        ctx.report(0, total)

    # C-N 歸因分類（依 RULE_CODES 順序取 C- 開頭者）；分頁名＝「code label」對齊規則管理 UI 左選單。
    for code in c_codes:
        if ctx is not None:
            ctx.check()
        content = db.get_rule_active(code)
        if content and content.get("tree"):
            l1 = content["tree"][0]
            # 域名優先取 tree[0].label（＝L1 域節點名，也是 UI 左選單/樹/判決/歸因列表的 SSOT，見
            # db.list_rule_meta 同序 coalesce），與使用者所見一致；缺則退 _meta.label。兩者不得反序，
            # 否則使用者於 UI 改樹節點 label 後導出仍撈到過時 _meta.label（曾漂移：商品品質 vs 商品服務品質）。
            l1_label = l1.get("label") or (content.get("_meta") or {}).get("label") or ""
            rows: list[list[str]] = []
            _collect(l1, f"{code} {l1_label}".strip(), "", rows)  # L1 cell 帶 C-N code
            title = f"{code} {l1_label}".strip()[:31]  # 分頁名上限 31 字
            ws = wb.create_sheet(title)
            ws.append(_TREE_HEADERS)
            for r in rows:
                ws.append(r)
            _style_header(ws, _TREE_WIDTHS, freeze_cols=3)  # 凍結 L1/L2/L3 三 code 欄
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
