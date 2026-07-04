"""list_problems（source_registry 分支）與 insert_source_batch upsert 語義測試。

需 temp_db fixture（隔離 PostgreSQL 測試庫）：驗證 score/product_vertical/日期區間/judged 篩選的
SQL 正確性，以及同一特徵 id（rec_oid）重複匯入的 upsert 覆蓋行為（總筆數不變）。
"""

from __future__ import annotations

from sqlalchemy import select

from app.core import db
from app.core import tables as T
from app.core.schema import TicketFinding


def _pr_row(rec_oid: str = "REC1", **overrides) -> dict:
    """建一筆 product_reviews 源列（現行拆表 schema：源欄名、值皆 Text）。"""
    base = {
        "rec_oid": rec_oid,
        "member_uuid": "U1",
        "create_date": "2026-06-01 10:00:00",
        "rec_title": "標題",
        "rec_desc": "內容",
        "rec_scores": "5",
        "traveller_type": "solo",
        "lang_code": "zh-tw",
        "prod_oid": "P1",
        "pkg_oid": "PKG1",
        "order_oid": "O1",
        "order_mid": "M1",
        "supplier_oid": "S1",
        "order_snap_json": "{}",
        "lst_dt_go": "2026-07-01",
        "product_category": '{"main": "CATEGORY_019"}',  # 現行存 JSON（_parse_category_main 取 .main）
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


def test_insert_source_batch_upsert_overwrites_and_preserves_count(temp_db) -> None:
    """同一 rec_oid 匯入兩次：第二次覆蓋第一次，總筆數不變（覆蓋非新增）。"""
    db.insert_source_batch("product_reviews", [_pr_row(rec_desc="第一版", rec_scores="3")])
    with T.get_engine().connect() as c:
        row1 = c.execute(
            select(T.product_reviews).where(T.product_reviews.c.rec_oid == "REC1")
        ).mappings().first()
    assert row1["rec_desc"] == "第一版"
    assert row1["rec_scores"] == "3"

    db.insert_source_batch("product_reviews", [_pr_row(rec_desc="第二版", rec_scores="5")])
    with T.get_engine().connect() as c:
        total = c.execute(select(T.product_reviews)).mappings().all()
        row2 = c.execute(
            select(T.product_reviews).where(T.product_reviews.c.rec_oid == "REC1")
        ).mappings().first()
    assert len(total) == 1  # 總筆數不變（衝突鍵 rec_oid 覆蓋）
    assert row2["rec_desc"] == "第二版"
    assert row2["rec_scores"] == "5"


def test_insert_source_batch_empty_list_returns_zero(temp_db) -> None:
    """空清單直接回 0，不觸碰 DB。"""
    assert db.insert_source_batch("product_reviews", []) == 0


def test_list_problems_source_registry_score_filter(temp_db) -> None:
    """source='product_reviews' + score 篩選：只回符合星等（rec_scores）的列。"""
    db.insert_source_batch(
        "product_reviews",
        [_pr_row(rec_oid="R1", rec_scores="5"), _pr_row(rec_oid="R2", rec_scores="2")],
    )
    result = db.list_problems(source="product_reviews", score=[5])
    assert result["total"] == 1
    assert result["rows"][0]["_group"] == "R1"


def test_list_problems_source_registry_product_vertical_filter(temp_db, monkeypatch) -> None:
    """source='product_reviews' + product_vertical='Tour'：依 product_vertical 分組展開代碼篩選。"""
    _seed_product_vertical(monkeypatch)
    db.insert_source_batch(
        "product_reviews",
        [
            _pr_row(rec_oid="R1", product_category='{"main": "CATEGORY_019"}'),
            _pr_row(rec_oid="R2", product_category='{"main": "CATEGORY_002"}'),
        ],
    )
    result = db.list_problems(source="product_reviews", product_vertical="Tour")
    assert result["total"] == 1
    assert result["rows"][0]["_group"] == "R1"


def test_list_problems_source_registry_date_range_filter(temp_db) -> None:
    """source='product_reviews' + date_from/date_to：依 create_date 區間篩選（含端點）。"""
    db.insert_source_batch(
        "product_reviews",
        [
            _pr_row(rec_oid="R1", create_date="2026-05-01 10:00:00"),
            _pr_row(rec_oid="R2", create_date="2026-06-15 10:00:00"),
        ],
    )
    result = db.list_problems(source="product_reviews", date_from="2026-06-01", date_to="2026-06-30")
    assert result["total"] == 1
    assert result["rows"][0]["_group"] == "R2"


def test_list_problems_source_registry_judged_filter(temp_db) -> None:
    """source='product_reviews' + judged：僅回有 / 無對應 judgments 列者。"""
    db.insert_source_batch("product_reviews", [_pr_row(rec_oid="R1"), _pr_row(rec_oid="R2")])
    db.insert_finding(
        TicketFinding(
            finding_id="fd_product_reviews_R1",
            ticket_id="R1",  # source_id（product_reviews→rec_oid）
            dimension="non_content",
            recommended_action="no_action",
        ),
        "product_reviews",
    )

    judged = db.list_problems(source="product_reviews", judged=True)
    assert judged["total"] == 1
    assert judged["rows"][0]["_group"] == "R1"

    unjudged = db.list_problems(source="product_reviews", judged=False)
    assert unjudged["total"] == 1
    assert unjudged["rows"][0]["_group"] == "R2"


def test_prejudge_target_ids_uses_registry_for_product_reviews(temp_db) -> None:
    """prejudge_target_ids(source='product_reviews') 走專表 join，只回未判 source_id。"""
    db.insert_source_batch("product_reviews", [_pr_row(rec_oid="R1"), _pr_row(rec_oid="R2")])
    db.insert_finding(
        TicketFinding(
            finding_id="fd_product_reviews_R1",
            ticket_id="R1",
            dimension="non_content",
            recommended_action="no_action",
        ),
        "product_reviews",
    )
    ids = db.prejudge_target_ids("product_reviews", stages=["unjudged"])
    assert ids == ["R2"]


def test_get_items_by_ids_uses_registry_for_product_reviews(temp_db) -> None:
    """get_items_by_ids(ids, source='product_reviews') 走專表，回傳列含源欄位（如 rec_scores）。"""
    db.insert_source_batch("product_reviews", [_pr_row(rec_oid="R1", rec_scores="4")])
    rows = db.get_items_by_ids(["R1"], source="product_reviews")
    assert len(rows) == 1
    assert rows[0]["rec_scores"] == "4"


def test_get_items_by_ids_none_source_returns_empty(temp_db) -> None:
    """source=None（縱覽全部）無單表可查，空清單安全回 []。"""
    assert db.get_items_by_ids([], source=None) == []
    assert db.get_items_by_ids(["not-exist"], source=None) == []
