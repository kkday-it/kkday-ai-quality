"""初判規則導出：初判 Prompt 包（zip）＋ 共用 Excel 導出樣式 helper。

Prompt-as-Source 架構下初判 prompt 唯一真相源＝prompts/*.md，故規則配置頁「導出」改為直接
打包該目錄（`build_prompts_zip_bytes`），不再派生 xlsx 結構表。`_style_header` 為各 Excel 導出（問題
列表 db/export、未來其他）共用的視覺美化 helper，續留本模組供 `db/export` 複用；openpyxl 為重庫，於
`_style_header` 內 lazy import。
"""

from __future__ import annotations

import io
import zipfile
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.export_jobs import ExportCtx


def _style_header(ws, widths: list[int], freeze_cols: int = 0) -> None:
    """統一導出美化樣式：表頭品牌綠底白字 ＋ 凍結首列(+前 freeze_cols code 欄) ＋ 篩選箭頭
    ＋ 全表細邊框 ＋ 資料列斑馬紋 ＋ 欄寬 ＋ 內容自動換行頂對齊。所有 Excel 導出共用此 helper 確保視覺一致。"""
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


def build_prompts_zip_bytes(ctx: ExportCtx | None = None) -> bytes:
    """打包 prompts 初判 prompt 目錄為 zip（bytes）：7 支 prompt md ＋ README ＋ BASELINE。

    Prompt-as-Source 架構下初判 prompt 唯一真相源＝prompts/*.md（見 `judge.prompt_source`），本
    導出直接打包該目錄的 .md 檔（含引擎契約 README、基線指標 BASELINE），供離線交付 / 版本留存 / 手動
    diff。以**磁碟現行檔**為準（DB 熱編 active 版另存 judge_rule_versions；若已在 RuleManager 熱編而未
    回寫檔，兩者可能不同步——需回寫請先「恢復默認」反向操作，或改由檔案編輯流程）。

    Args:
        ctx: 背景 job 進度把手（可選）；給定時每打包一檔回報進度並輪詢取消，None＝同步直呼。

    Returns:
        zip 檔的位元組內容（供 API 以 attachment 回傳）。

    Raises:
        Cancelled: ctx 對應 job 被取消時由 ctx.check() 拋出。
    """
    from app.core.paths import PROMPTS_DIR

    # 只收 .md（7 支 prompt + README + BASELINE）；.DS_Store 等非 md 與既有 zip 自然排除。保序打包利穩定 diff。
    files = sorted(PROMPTS_DIR.glob("*.md"))
    total = len(files)
    if ctx is not None:
        ctx.report(0, total)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, path in enumerate(files, start=1):
            if ctx is not None:
                ctx.check()
            zf.write(path, arcname=path.name)  # 扁平置於 zip 根，檔名對齊 prompts 佈局
            if ctx is not None:
                ctx.report(i, total)
    return buf.getvalue()
