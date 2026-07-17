"""導出深化回歸：C-1~C-6 六域命中欄（符合/不符合）＋「分類統計」表名＋「Prompts」版本快照表。

六域欄為 review 級（合併儲存格）、值供 Excel 篩選：已判評論逐域 符合/不符合、
完全未判評論六欄空白；Prompts 表輸出 7 支判決 prompt 的 active 版本溯源
（測試庫無 DB 版 → 版本欄「檔案默認」、內容回退 prompts/*.md）。
"""

from __future__ import annotations

import io

from openpyxl import load_workbook

from app.core import db
from app.core.schema import TicketFinding


def _pr_row(rec_oid: str) -> dict:
    return {
        "rec_oid": rec_oid,
        "create_date": "2026-06-10 08:30:00",
        "rec_desc": "描述與實際不符",
        "rec_scores": "1",
        "prod_oid": "P1",
        "order_snap_json": "{}",
    }


def _finding(rec_oid: str, l1_code: str, l1_label: str) -> TicketFinding:
    return TicketFinding(
        finding_id=f"fd_product_reviews_{rec_oid}__{l1_code}",
        ticket_id=rec_oid,
        recommended_action="no_action",
        polarity="negative",
        sentiment_score=1,
        l1_domain_code=l1_code,
        l1_label=l1_label,
        confidence=0.9,
        raw_confidence=0.9,
        confidence_tier="auto_accept",
        judgment_stage="judged",
        summary={"zh-tw": "測試摘要"},
        model_used="gpt-5-mini",
    )


def _sheet_cells(blob: bytes) -> tuple[list, list[list], list[str]]:
    """xlsx bytes → (資料表表頭, 資料列, 全部工作表名)。"""
    wb = load_workbook(io.BytesIO(blob))
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    return list(rows[0]), [list(r) for r in rows[1:]], wb.sheetnames


def test_domain_columns_judged_vs_unjudged(temp_db) -> None:
    """已判評論：命中域＝符合、其餘＝不符合；未判評論：六欄全空白。"""
    db.insert_source_batch("product_reviews", [_pr_row("D1"), _pr_row("D2")])
    # D1 判為 content + service 雙域；D2 完全未判
    db.replace_source_findings(
        "product_reviews",
        "D1",
        [_finding("D1", "content", "商品內容"), _finding("D1", "service", "客服營運")],
    )
    headers, rows, _names = _sheet_cells(db.export_problems_xlsx(source="product_reviews"))
    dom_idx = {h.split(" ")[0]: i for i, h in enumerate(headers) if str(h).startswith("C-")}
    assert set(dom_idx) == {"C-1", "C-2", "C-3", "C-4", "C-5", "C-6"}
    d1 = next(r for r in rows if r[0] == "D1")
    assert d1[dom_idx["C-1"]] == "符合" and d1[dom_idx["C-5"]] == "符合"
    for cn in ("C-2", "C-3", "C-4", "C-6"):
        assert d1[dom_idx[cn]] == "不符合"
    d2 = next(r for r in rows if r[0] == "D2")
    assert all(not d2[dom_idx[cn]] for cn in dom_idx)  # 未判 → 空白（None/""）


def test_stats_renamed_and_prompts_sheet_appended(temp_db) -> None:
    """工作表：資料表 →「分類統計」→「Prompts」；不得再出現舊名「歸因統計」。"""
    db.insert_source_batch("product_reviews", [_pr_row("D3")])
    db.replace_source_findings("product_reviews", "D3", [_finding("D3", "content", "商品內容")])
    _h, _r, names = _sheet_cells(db.export_problems_xlsx(source="product_reviews"))
    assert "分類統計" in names and "Prompts" in names
    assert "歸因統計" not in names
    assert names.index("分類統計") < names.index("Prompts")  # Prompts 於統計之後


def test_prompts_sheet_lists_all_seven_with_version(temp_db) -> None:
    """Prompts 表：7 支 prompt 一列一支（rule_code 全到齊），測試庫無 DB 版 → 版本欄「檔案默認」、
    內容全文非空（回退 prompts/*.md）。"""
    from app.judge import prompt_source

    db.insert_source_batch("product_reviews", [_pr_row("D4")])
    db.replace_source_findings("product_reviews", "D4", [_finding("D4", "content", "商品內容")])
    wb = load_workbook(io.BytesIO(db.export_problems_xlsx(source="product_reviews")))
    ws = wb["Prompts"]
    rows = [list(r) for r in ws.iter_rows(values_only=True)][1:]
    assert [r[0] for r in rows] == list(prompt_source.PROMPT_RULE_CODES)
    for r in rows:
        assert r[2] == "檔案默認"  # temp_db 無 active 版
        assert r[5] and len(str(r[5])) > 100  # 內容全文回退檔案、非空


def test_prompts_sheet_version_is_release_timestamp(temp_db) -> None:
    """有 DB active 版：版本欄＝發版時間戳（v+14 位數字，UTC），不輸出 per-rule 整數流水號。"""
    import re

    from app.core import paths
    from app.judge import prompt_source

    db.insert_source_batch("product_reviews", [_pr_row("D5")])
    db.replace_source_findings("product_reviews", "D5", [_finding("D5", "content", "商品內容")])
    # seed 真實 md 為 active 版（假內容會使 structure() 的 DB-first 解析炸掉）
    md = (paths.PROMPTS_DIR / "01_C-1_content.md").read_text(encoding="utf-8")
    db.save_rule_version(
        "prompt_C-1", {"text": md, "_meta": {"label": "商品內容"}}, note="測試發版"
    )
    prompt_source.reload()  # 清解析快取，使 load 走 DB-first
    try:
        wb = load_workbook(io.BytesIO(db.export_problems_xlsx(source="product_reviews")))
        rows = [list(r) for r in wb["Prompts"].iter_rows(values_only=True)][1:]
        c1 = next(r for r in rows if r[0] == "prompt_C-1")
        assert re.fullmatch(r"v\d{14}", str(c1[2]))  # 發版時間戳，非 v1 流水號
        assert (
            next(r for r in rows if r[0] == "prompt_polarity")[2] == "檔案默認"
        )  # 未發版者不受影響
    finally:
        prompt_source.reload()  # 還原快取（temp_db 結束後 DB 版消失，避免污染後續測試）
