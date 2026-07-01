"""list_problems（source_registry 分支）與 insert_product_reviews_batch upsert 語義測試。

需 temp_db fixture（隔離 PostgreSQL 測試庫）：驗證 score/category_group/日期區間篩選的
SQL 正確性，以及同一 source_record_id 重複匯入的 upsert 覆蓋行為（xid 不變、總筆數不變）。
"""

from __future__ import annotations

from sqlalchemy import select

from app.core import db
from app.core import tables as T
from app.core.schema import TicketFinding


def _pr_row(**overrides) -> dict:
    """建一筆 product_reviews 測試列（預設值 + overrides 覆蓋）。"""
    base = {
        "source_record_id": "REC1",
        "item_id": "product_reviews-REC1",
        "member_uuid": "U1",
        "traveller_type": "solo",
        "lang": "zh-tw",
        "occurred_at": "2026-06-01 10:00:00",
        "title": "標題",
        "content": "內容",
        "score": 5,
        "prod_oid": "P1",
        "pkg_oid": "PKG1",
        "order_oid": "O1",
        "order_mid": "M1",
        "supplier_oid": "S1",
        "product_category_main": "CATEGORY_019",
        "product_category_sub": ["CATEGORY_019A"],
        "go_date": "2026-07-01",
        "prod_name_snapshot": {"zh-tw": {"prod_name": "測試商品"}},
        "status": "pending",
        "created_at": "2026-06-01T10:00:00",
        "raw": "{}",
    }
    base.update(overrides)
    return base


def _seed_product_vertical(monkeypatch) -> None:
    """注入 db.get_rule_active('product_vertical') 供 product_vertical 篩選測試（版本化規則 loader）。"""
    monkeypatch.setattr(
        db,
        "get_rule_active",
        lambda code: {"groups": {"Tour": ["CATEGORY_019"], "Tix": ["CATEGORY_002"]}}
        if code == "product_vertical"
        else None,
    )


def test_insert_product_reviews_batch_upsert_preserves_xid_and_count(temp_db) -> None:
    """同一 source_record_id 匯入兩次：第二次內容覆蓋第一次，xid 不變，總筆數不變。"""
    db.insert_product_reviews_batch([_pr_row(content="第一版內容", score=3)])
    with T.get_engine().connect() as c:
        row1 = c.execute(
            select(T.product_reviews).where(T.product_reviews.c.source_record_id == "REC1")
        ).mappings().first()
    assert row1["content"] == "第一版內容"
    assert row1["score"] == 3
    xid_before = row1["xid"]

    db.insert_product_reviews_batch([_pr_row(content="第二版內容", score=5)])
    with T.get_engine().connect() as c:
        total = c.execute(select(T.product_reviews)).mappings().all()
        row2 = c.execute(
            select(T.product_reviews).where(T.product_reviews.c.source_record_id == "REC1")
        ).mappings().first()
    assert len(total) == 1  # 總筆數不變（覆蓋非新增）
    assert row2["xid"] == xid_before  # xid 保留
    assert row2["content"] == "第二版內容"
    assert row2["score"] == 5


def test_insert_product_reviews_batch_empty_list_returns_zero(temp_db) -> None:
    """空清單直接回 0，不觸碰 DB。"""
    assert db.insert_product_reviews_batch([]) == 0


def test_list_problems_source_registry_score_filter(temp_db) -> None:
    """source='product_reviews' + score 篩選：只回符合星等的列。"""
    db.insert_product_reviews_batch(
        [
            _pr_row(source_record_id="R1", item_id="product_reviews-R1", score=5),
            _pr_row(source_record_id="R2", item_id="product_reviews-R2", score=2),
        ]
    )
    result = db.list_problems(source="product_reviews", score=[5])
    assert result["total"] == 1
    assert result["rows"][0]["item_id"] == "product_reviews-R1"


def test_list_problems_source_registry_product_vertical_filter(temp_db, monkeypatch) -> None:
    """source='product_reviews' + product_vertical='Tour'：依 product_vertical 分組展開代碼篩選。"""
    _seed_product_vertical(monkeypatch)
    db.insert_product_reviews_batch(
        [
            _pr_row(
                source_record_id="R1",
                item_id="product_reviews-R1",
                product_category_main="CATEGORY_019",
            ),
            _pr_row(
                source_record_id="R2",
                item_id="product_reviews-R2",
                product_category_main="CATEGORY_002",
            ),
        ]
    )
    result = db.list_problems(source="product_reviews", product_vertical="Tour")
    assert result["total"] == 1
    assert result["rows"][0]["item_id"] == "product_reviews-R1"


def test_list_problems_source_registry_date_range_filter(temp_db) -> None:
    """source='product_reviews' + date_from/date_to：依 occurred_at 區間篩選（含端點）。"""
    db.insert_product_reviews_batch(
        [
            _pr_row(
                source_record_id="R1",
                item_id="product_reviews-R1",
                occurred_at="2026-05-01 10:00:00",
            ),
            _pr_row(
                source_record_id="R2",
                item_id="product_reviews-R2",
                occurred_at="2026-06-15 10:00:00",
            ),
        ]
    )
    result = db.list_problems(source="product_reviews", date_from="2026-06-01", date_to="2026-06-30")
    assert result["total"] == 1
    assert result["rows"][0]["item_id"] == "product_reviews-R2"


def test_list_problems_source_registry_judged_filter(temp_db) -> None:
    """source='product_reviews' + judged=True：僅回有對應 judgments 列者。"""
    db.insert_product_reviews_batch(
        [
            _pr_row(source_record_id="R1", item_id="product_reviews-R1"),
            _pr_row(source_record_id="R2", item_id="product_reviews-R2"),
        ]
    )
    finding = TicketFinding(
        finding_id="fd_product_reviews-R1",
        ticket_id="product_reviews-R1",
        dimension="non_content",
        recommended_action="no_action",
    )
    db.insert_finding(finding, "product_reviews")

    judged = db.list_problems(source="product_reviews", judged=True)
    assert judged["total"] == 1
    assert judged["rows"][0]["item_id"] == "product_reviews-R1"

    unjudged = db.list_problems(source="product_reviews", judged=False)
    assert unjudged["total"] == 1
    assert unjudged["rows"][0]["item_id"] == "product_reviews-R2"


def test_unjudged_item_ids_uses_registry_for_product_reviews(temp_db) -> None:
    """unjudged_item_ids(source='product_reviews') 走專表 join，只回未判 item_id。"""
    db.insert_product_reviews_batch(
        [
            _pr_row(source_record_id="R1", item_id="product_reviews-R1"),
            _pr_row(source_record_id="R2", item_id="product_reviews-R2"),
        ]
    )
    finding = TicketFinding(
        finding_id="fd_product_reviews-R1",
        ticket_id="product_reviews-R1",
        dimension="non_content",
        recommended_action="no_action",
    )
    db.insert_finding(finding, "product_reviews")

    ids = db.unjudged_item_ids("product_reviews")
    assert ids == ["product_reviews-R2"]


def test_get_items_by_ids_uses_registry_for_product_reviews(temp_db) -> None:
    """get_items_by_ids(ids, source='product_reviews') 走專表，回傳列含專表欄位（如 score）。"""
    db.insert_product_reviews_batch([_pr_row(source_record_id="R1", item_id="product_reviews-R1", score=4)])
    rows = db.get_items_by_ids(["product_reviews-R1"], source="product_reviews")
    assert len(rows) == 1
    assert rows[0]["score"] == 4


def test_get_items_by_ids_fallback_none_source(temp_db) -> None:
    """source=None 時 fallback 沿用 get_inbound_by_ids（intake_items 邏輯，空清單安全回 []）。"""
    assert db.get_items_by_ids([], source=None) == []
    assert db.get_items_by_ids(["not-exist"], source=None) == []
