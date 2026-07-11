"""導出「輸出結果版本」（snapshot_model 歷史快照替換）+ xlsx 欄位回歸測試。

覆蓋：快照模式內容/底色列傾向同步/覆核軸空白/未命中排除/統計附註；
當前判決模式「覆核狀態/真值」欄非空（鎖住 _attr_keys 既有 bug 修復——曾因缺 key
fallback 讀 row 級恆 None 而靜默空白）。
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


def _finding(
    rec_oid: str,
    model: str,
    l1_code: str = "content",
    l1_label: str = "商品內容",
    polarity: str = "negative",
    summary: str = "頁面資訊與現場不符",
) -> TicketFinding:
    return TicketFinding(
        finding_id=f"fd_product_reviews_{rec_oid}__{l1_code}",
        ticket_id=rec_oid,
        dimension="non_content",
        recommended_action="no_action",
        polarity=polarity,
        sentiment_score=1 if polarity == "negative" else 4,
        l1_domain_code=l1_code,
        l1_label=l1_label,
        confidence=0.9,
        raw_confidence=0.9,
        confidence_tier="auto_accept",
        judgment_stage="judged",
        summary={"zh-tw": summary},
        model_used=model,
    )


def _cells(blob: bytes) -> tuple[list, list[list]]:
    """xlsx bytes → (表頭列, 資料列 list)。"""
    wb = load_workbook(io.BytesIO(blob))
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    return list(rows[0]), [list(r) for r in rows[1:]]


def _col(headers: list, name: str) -> int:
    return headers.index(name)


def test_export_current_mode_status_and_model_columns(temp_db) -> None:
    """當前判決模式：「判決模型」「覆核狀態」「真值」欄有值（_attr_keys 缺 key 舊 bug 回歸鎖定）。"""
    db.insert_source_batch("product_reviews", [_pr_row("E1")])
    db.replace_source_findings("product_reviews", "E1", [_finding("E1", "gpt-5-mini")])
    db.update_finding_status("fd_product_reviews_E1__content", "confirmed", actor="qa@kkday.com")
    db.update_finding_true_label("fd_product_reviews_E1__content", "content")
    headers, rows = _cells(db.export_problems_xlsx(source="product_reviews"))
    assert "判決模型" in headers and "覆核狀態" in headers and "真值" in headers
    r = rows[0]
    assert r[_col(headers, "判決模型")] == "gpt-5-mini"
    assert r[_col(headers, "覆核狀態")] == "已確認"  # _STATUS_LABEL_ZH 中文化
    assert r[_col(headers, "真值")] == "content"


def test_export_snapshot_model_replaces_content(temp_db) -> None:
    """快照模式：內容/列傾向替換為所選模型快照；覆核軸空白；無該模型快照的評論整列排除。"""
    db.insert_source_batch("product_reviews", [_pr_row("S1"), _pr_row("S2")])
    # S1：先 gpt-5-mini 判 content/負向 → 再 seed 重判 supplier/正向（當前判決＝seed）
    db.replace_source_findings("product_reviews", "S1", [_finding("S1", "gpt-5-mini")])
    db.update_finding_status("fd_product_reviews_S1__content", "confirmed", actor="qa@kkday.com")
    db.replace_source_findings(
        "product_reviews",
        "S1",
        [_finding("S1", "seed-2-0-lite", "supplier", "供應商履約", "positive", "他模型觀點")],
    )
    # S2：只有 gpt-5-mini 判過 → 選 seed 輸出時應被排除
    db.replace_source_findings("product_reviews", "S2", [_finding("S2", "gpt-5-mini")])

    headers, rows = _cells(
        db.export_problems_xlsx(source="product_reviews", snapshot_model="seed-2-0-lite")
    )
    assert len(rows) == 1  # S2 無 seed 快照 → 排除
    r = rows[0]
    assert r[_col(headers, "編號")] == "S1"
    assert r[_col(headers, "L1 分類")] == "供應商履約"  # 快照內容（非當前判決也非舊 gpt 判決）
    assert r[_col(headers, "問題摘要")] == "他模型觀點"
    assert r[_col(headers, "判決模型")] == "seed-2-0-lite"
    assert r[_col(headers, "情緒傾向")] == "4"  # row 級 our_sentiment 同步為快照 primary
    # 覆核軸屬「當前判決」語義，歷史快照不冒充 → 兩欄空白
    assert not r[_col(headers, "覆核狀態")] and not r[_col(headers, "真值")]


def test_export_snapshot_selects_that_models_view(temp_db) -> None:
    """快照模式選舊模型：輸出該模型當時判的內容（與當前判決不同），統計附註揭露口徑。"""
    db.insert_source_batch("product_reviews", [_pr_row("S3")])
    db.replace_source_findings(
        "product_reviews", "S3", [_finding("S3", "gpt-5-mini", summary="gpt 觀點")]
    )
    db.replace_source_findings(
        "product_reviews",
        "S3",
        [_finding("S3", "seed-2-0-lite", summary="seed 觀點")],
    )
    blob = db.export_problems_xlsx(source="product_reviews", snapshot_model="gpt-5-mini")
    headers, rows = _cells(blob)
    assert rows[0][_col(headers, "問題摘要")] == "gpt 觀點"  # 舊模型快照，非當前判決（seed）
    # 統計表 A2 揭露輸出版本口徑
    wb = load_workbook(io.BytesIO(blob))
    a2 = wb["歸因統計"]["A2"].value
    assert "gpt-5-mini" in a2 and "已排除 0 則" in a2
