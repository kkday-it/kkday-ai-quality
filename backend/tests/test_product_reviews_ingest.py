"""product_reviews_ingest.row_to_product_review 單元測試（純函式，無需資料庫）。"""

from __future__ import annotations

import json

from app.judge.ingest import product_reviews as product_reviews_ingest


def test_row_to_product_review_normal_case() -> None:
    """正常 case：完整 canonical + raw → 各欄位正確映射，item_id 決定性生成。"""
    canon = {
        "source": "product_reviews",
        "source_record_id": "REC123",
        "occurred_at": "2026-06-01 10:00:00",
        "title": "不錯的行程",
        "content": "整體體驗良好",
        "score": "5",
        "prod_oid": "P1",
        "pkg_oid": "PKG1",
        "order_oid": "O1",
        "supplier_oid": "S1",
        "member_uuid": "U1",
        "lang": "zh-tw",
    }
    raw = {
        "rec_oid": "REC123",
        "order_mid": "M1",
        "traveller_type": "family",
        "lst_dt_go": "2026-07-01",
        "product_category": json.dumps({"main": "CATEGORY_019", "sub": ["CATEGORY_019A"]}),
        "order_snap_json": json.dumps({"zh-tw": {"prod_name": "測試商品"}}),
    }
    out = product_reviews_ingest.row_to_product_review(canon, raw)
    assert out["item_id"] == "product_reviews-REC123"
    assert out["source_record_id"] == "REC123"
    assert out["score"] == 5
    assert out["prod_oid"] == "P1"
    assert out["order_mid"] == "M1"
    assert out["traveller_type"] == "family"
    assert out["go_date"] == "2026-07-01"
    assert out["product_category_main"] == "CATEGORY_019"
    assert out["product_category_sub"] == ["CATEGORY_019A"]
    assert out["prod_name_snapshot"] == {"zh-tw": {"prod_name": "測試商品"}}
    assert out["status"] == "pending"


def test_row_to_product_review_dirty_category_field() -> None:
    """product_category 為非 JSON 字串 / 缺欄時防禦式處理，不炸整批匯入。"""
    canon = {"source_record_id": "REC999", "content": "x"}
    raw = {"rec_oid": "REC999", "product_category": "not-a-json-string"}
    out = product_reviews_ingest.row_to_product_review(canon, raw)
    assert out["product_category_main"] is None
    assert out["product_category_sub"] == []


def test_row_to_product_review_missing_category_field() -> None:
    """product_category 完全缺欄（raw 無此 key）回 (None, [])，不拋 KeyError。"""
    canon = {"source_record_id": "REC888", "content": "y"}
    raw = {"rec_oid": "REC888"}
    out = product_reviews_ingest.row_to_product_review(canon, raw)
    assert out["product_category_main"] is None
    assert out["product_category_sub"] == []


def test_row_to_product_review_no_rec_oid_yields_empty_item_id() -> None:
    """無自然鍵（rec_oid 缺失）時 item_id/source_record_id 為 None，供呼叫端過濾。"""
    canon = {"content": "z"}
    raw = {}
    out = product_reviews_ingest.row_to_product_review(canon, raw)
    assert out["item_id"] is None
    assert out["source_record_id"] is None


def test_parse_score_handles_dirty_values() -> None:
    """星等解析：空值/非數字回 None，數字字串正確轉 int。"""
    assert product_reviews_ingest._parse_score("4") == 4
    assert product_reviews_ingest._parse_score("") is None
    assert product_reviews_ingest._parse_score(None) is None
    assert product_reviews_ingest._parse_score("not-a-number") is None
