"""list_problems（source_registry 分支）與 insert_source_batch upsert 語義測試。

需 temp_db fixture（隔離 PostgreSQL 測試庫）：驗證 score/product_vertical/日期區間/judged 篩選的
SQL 正確性，以及同一特徵 id（rec_oid）重複匯入的 upsert 覆蓋行為（總筆數不變）。
"""

from __future__ import annotations

from sqlalchemy import select

from app.core import db
from app.core.db import tables as T
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
        lambda code: (
            {"groups": {"Tour": ["CATEGORY_019"], "Tix": ["CATEGORY_002"]}}
            if code == "product_vertical"
            else None
        ),
    )


def test_insert_source_batch_upsert_overwrites_and_preserves_count(temp_db) -> None:
    """同一 rec_oid 匯入兩次：第二次覆蓋第一次，總筆數不變（覆蓋非新增）。"""
    db.insert_source_batch("product_reviews", [_pr_row(rec_desc="第一版", rec_scores="3")])
    with T.get_engine().connect() as c:
        row1 = (
            c.execute(select(T.product_reviews).where(T.product_reviews.c.rec_oid == "REC1"))
            .mappings()
            .first()
        )
    assert row1["rec_desc"] == "第一版"
    assert row1["rec_scores"] == "3"

    db.insert_source_batch("product_reviews", [_pr_row(rec_desc="第二版", rec_scores="5")])
    with T.get_engine().connect() as c:
        total = c.execute(select(T.product_reviews)).mappings().all()
        row2 = (
            c.execute(select(T.product_reviews).where(T.product_reviews.c.rec_oid == "REC1"))
            .mappings()
            .first()
        )
    assert len(total) == 1  # 總筆數不變（衝突鍵 rec_oid 覆蓋）
    assert row2["rec_desc"] == "第二版"
    assert row2["rec_scores"] == "5"


def test_insert_source_batch_empty_list_returns_zero(temp_db) -> None:
    """空清單直接回 0，不觸碰 DB。"""
    assert db.insert_source_batch("product_reviews", []) == 0


def test_list_problems_source_registry_taxonomy_filter(temp_db) -> None:
    """source='product_reviews' + taxonomy 篩選：任意層級 code（l1/l2/l3_code 任一 IN）子樹語義。"""
    db.insert_source_batch(
        "product_reviews",
        [_pr_row(rec_oid="R1", rec_scores="5"), _pr_row(rec_oid="R2", rec_scores="2")],
    )
    # R1 判到 L2（l3 空）；R2 判另一域 → 篩 L1 'content' 應命中 R1（涵蓋只判到 L2 的列）
    db.insert_finding(
        TicketFinding(
            finding_id="fd_product_reviews_R1",
            ticket_id="R1",
            recommended_action="no_action",
            polarity="negative",
            l1_domain_code="content",
            l1_label="商品內容",
            l2_code="C-1-2",
            l2_label="行程資訊",
        ),
        "product_reviews",
    )
    db.insert_finding(
        TicketFinding(
            finding_id="fd_product_reviews_R2",
            ticket_id="R2",
            recommended_action="no_action",
            polarity="negative",
            l1_domain_code="supplier",
            l1_label="供應商履約",
        ),
        "product_reviews",
    )
    # L1 code 命中（子樹語義）
    r = db.list_problems(source="product_reviews", taxonomy=["content"])
    assert r["total"] == 1 and r["rows"][0]["_group"] == "R1"
    # L2 code 命中
    r = db.list_problems(source="product_reviews", taxonomy=["C-1-2"])
    assert r["total"] == 1 and r["rows"][0]["_group"] == "R1"
    # 多選 OR：兩域都中
    r = db.list_problems(source="product_reviews", taxonomy=["content", "supplier"])
    assert r["total"] == 2


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
    result = db.list_problems(
        source="product_reviews", date_from="2026-06-01", date_to="2026-06-30"
    )
    assert result["total"] == 1
    assert result["rows"][0]["_group"] == "R2"


def test_list_problems_source_registry_judged_filter(temp_db) -> None:
    """source='product_reviews' + judged：僅回有 / 無對應 judgments 列者。"""
    db.insert_source_batch("product_reviews", [_pr_row(rec_oid="R1"), _pr_row(rec_oid="R2")])
    db.insert_finding(
        TicketFinding(
            finding_id="fd_product_reviews_R1",
            ticket_id="R1",  # source_id（product_reviews→rec_oid）
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
            recommended_action="no_action",
        ),
        "product_reviews",
    )
    ids = db.prejudge_target_ids("product_reviews", stages=["unjudged"])
    assert ids == ["R2"]


def test_prejudge_target_ids_has_external_filter(temp_db) -> None:
    """has_external 表級篩選（初判目標選取）：有外部融合內容的列才算「有」，空字串污染列不誤判。

    R1＝有外部（lst_oid + sentiment）；R2＝upsert 未匹配的空字串污染（三欄皆 ''，須視為「無」）；
    R3＝無融合資料（NULL）。驗證 True 只回 R1、False 回 R2+R3（與列表 SSOT apply_table_filters 同語義）。
    """
    db.insert_source_batch(
        "product_reviews",
        [_pr_row(rec_oid="R1"), _pr_row(rec_oid="R2"), _pr_row(rec_oid="R3")],
    )
    with T.get_engine().begin() as c:
        c.execute(
            T.product_reviews.update()
            .where(T.product_reviews.c.rec_oid == "R1")
            .values(review_external_lst_oid="EX1", sentiment="4", free_tag='["服務"]')
        )
        c.execute(
            T.product_reviews.update()
            .where(T.product_reviews.c.rec_oid == "R2")
            .values(review_external_lst_oid="", sentiment="", free_tag="")
        )
    ids_has = db.prejudge_target_ids("product_reviews", stages=["unjudged"], has_external=True)
    assert set(ids_has) == {"R1"}
    ids_no = db.prejudge_target_ids("product_reviews", stages=["unjudged"], has_external=False)
    assert set(ids_no) == {"R2", "R3"}
    # None＝不篩選：三列全回
    ids_all = db.prejudge_target_ids("product_reviews", stages=["unjudged"])
    assert set(ids_all) == {"R1", "R2", "R3"}


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


def test_list_problems_sort_by_confidence_no_correlation_error(temp_db) -> None:
    """sort_by=confidence 不再 500（scalar 子查詢 correlate_except judgments）＋依 item 最大信心排序。

    回歸鎖：_paged_fanout 外層 join judgments，confidence 排序子查詢若不指定 correlate 範圍，
    SQLAlchemy 會把子查詢的 judgments 也 auto-correlate 掉 → 「no FROM clauses」500。
    """
    db.insert_source_batch("product_reviews", [_pr_row(rec_oid="R1"), _pr_row(rec_oid="R2")])
    db.insert_finding(
        TicketFinding(
            finding_id="fd_product_reviews_R1",
            ticket_id="R1",
            recommended_action="no_action",
            confidence=0.3,
        ),
        "product_reviews",
    )
    db.insert_finding(
        TicketFinding(
            finding_id="fd_product_reviews_R2",
            ticket_id="R2",
            recommended_action="no_action",
            confidence=0.9,
        ),
        "product_reviews",
    )

    asc = db.list_problems(
        source="product_reviews", judged=True, sort_by="confidence", sort_dir="asc"
    )
    assert asc["total"] == 2
    assert [r["_group"] for r in asc["rows"]] == ["R1", "R2"]  # 0.3 在前

    desc = db.list_problems(
        source="product_reviews", judged=True, sort_by="confidence", sort_dir="desc"
    )
    assert [r["_group"] for r in desc["rows"]] == ["R2", "R1"]  # 0.9 在前


def test_prejudge_target_ids_full_dimension_filters(temp_db) -> None:
    """prejudge_target_ids 列表全維度篩選：表級（日期/prod_oid）兩分支皆套、判決級（tier/歸因分類）僅已判分支。"""
    db.insert_source_batch(
        "product_reviews",
        [
            _pr_row(rec_oid="R1", rec_scores="1", create_date="2026-07-01 09:00:00"),
            _pr_row(rec_oid="R2", rec_scores="5", create_date="2026-06-01 09:00:00"),
            _pr_row(rec_oid="R3", rec_scores="1", create_date="2026-07-02 09:00:00", prod_oid="P9"),
        ],
    )
    # R3 已判（負向 · pending_review · jury · content）；R1/R2 未判
    db.insert_finding(
        TicketFinding(
            finding_id="fd_product_reviews_R3",
            ticket_id="R3",
            recommended_action="no_action",
            polarity="negative",
            confidence=0.6,
            raw_confidence=0.6,
            confidence_tier="jury",
            judgment_stage="pending_review",
            l1_domain_code="content",
            l1_label="商品內容",
        ),
        "product_reviews",
    )

    # 未判分支 + 日期區間：只 R1（R2 在區間外）
    assert db.prejudge_target_ids(
        "product_reviews", stages=["unjudged"], date_from="2026-06-15", date_to="2026-07-01"
    ) == ["R1"]
    # 已判分支 + 判決級收斂（tier/L1 命中）→ R3；tier 不符 → 空
    assert db.prejudge_target_ids(
        "product_reviews",
        stages=["pending_review"],
        confidence_tier="jury",
        taxonomy=["content"],
    ) == ["R3"]
    assert (
        db.prejudge_target_ids(
            "product_reviews", stages=["pending_review"], confidence_tier="auto_accept"
        )
        == []
    )
    # 已判分支 + 表級 prod_oid：R3 有 P9
    assert db.prejudge_target_ids("product_reviews", stages=["pending_review"], prod_oid="P9") == [
        "R3"
    ]
    assert (
        db.prejudge_target_ids("product_reviews", stages=["pending_review"], prod_oid="NOPE") == []
    )


def test_prejudge_target_ids_within_ids_scope(temp_db) -> None:
    """within_ids 範圍收斂：目標選取僅在勾選列集合內（未判/已判分支皆套；空清單＝空範圍）。"""
    db.insert_source_batch(
        "product_reviews",
        [_pr_row(rec_oid="R1"), _pr_row(rec_oid="R2"), _pr_row(rec_oid="R3")],
    )
    # R3 已判（judged）；R1/R2 未判
    db.insert_finding(
        TicketFinding(
            finding_id="fd_product_reviews_R3",
            ticket_id="R3",
            recommended_action="no_action",
            judgment_stage="judged",
        ),
        "product_reviews",
    )
    # 未判分支 + within {R1,R3} → 只 R1（R2 不在範圍、R3 已判）
    assert db.prejudge_target_ids(
        "product_reviews", stages=["unjudged"], within_ids=["R1", "R3"]
    ) == ["R1"]
    # 全階段 + within {R1,R3} → R1+R3（整個勾選集合；R2 不在範圍）
    assert sorted(
        db.prejudge_target_ids(
            "product_reviews", stages=["unjudged", "judged"], within_ids=["R1", "R3"]
        )
    ) == ["R1", "R3"]
    # 空清單＝空範圍（非「不限」）
    assert db.prejudge_target_ids("product_reviews", stages=["unjudged"], within_ids=[]) == []


def test_list_problems_model_filter(temp_db) -> None:
    """model 篩選（judgments.model IN——當前判決維度）：單選/多選命中、未命中排除。"""
    db.insert_source_batch(
        "product_reviews",
        [_pr_row(rec_oid="M1", rec_scores="2"), _pr_row(rec_oid="M2", rec_scores="1")],
    )
    db.insert_finding(
        TicketFinding(
            finding_id="fd_product_reviews_M1",
            ticket_id="M1",
            recommended_action="no_action",
            polarity="negative",
            l1_domain_code="content",
            l1_label="商品內容",
            model_used="gpt-5-mini",
        ),
        "product_reviews",
    )
    db.insert_finding(
        TicketFinding(
            finding_id="fd_product_reviews_M2",
            ticket_id="M2",
            recommended_action="no_action",
            polarity="negative",
            l1_domain_code="supplier",
            l1_label="供應商履約",
            model_used="seed-2-0-lite",
        ),
        "product_reviews",
    )
    r = db.list_problems(source="product_reviews", model=["gpt-5-mini"])
    assert r["total"] == 1 and r["rows"][0]["_group"] == "M1"
    # DTO 帶 model（列表 model 標籤資料源）
    assert r["rows"][0]["attributions"][0]["model"] == "gpt-5-mini"
    r = db.list_problems(source="product_reviews", model=["gpt-5-mini", "seed-2-0-lite"])
    assert r["total"] == 2
    r = db.list_problems(source="product_reviews", model=["nonexistent"])
    assert r["total"] == 0
