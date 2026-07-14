"""歸因概覽聚合（db.attribution_overview）測試：KPI / 傾向 / L1 域分布 + 日期區間（含 Phase 1 sargable 改動）。

需 temp_db（隔離庫，合成拋棄列）。同時鎖定 Phase 1 效能改動語義：日期上界含當日整天、隔日排除
（`date_col < date_to||'~'` 取代 substr）。
"""

from __future__ import annotations

from app.core import db
from app.core.schema import TicketFinding


def _pr(rec_oid: str, create_date: str) -> dict:
    return {
        "rec_oid": rec_oid,
        "create_date": create_date,
        "rec_desc": "內容",
        "rec_scores": "3",
        "prod_oid": "P1",
        "order_snap_json": "{}",
    }


def _finding(
    rec_oid: str, polarity: str, l1_code: str = "", l1_label: str = "", conf: float = 0.9
) -> TicketFinding:
    return TicketFinding(
        finding_id=f"fd_product_reviews_{rec_oid}__{l1_code or 'none'}",
        ticket_id=rec_oid,
        recommended_action="no_action",
        polarity=polarity,
        l1_domain_code=l1_code,
        l1_label=l1_label,
        confidence=conf,
        raw_confidence=conf,
        confidence_tier="auto_accept",
        judgment_stage="judged",
    )


def _seed(temp_db) -> None:
    """R1 負向content / R2 正向未歸因 / R3 負向supplier（皆 6 月）+ R4 負向content（7 月·出區間）。"""
    db.insert_source_batch(
        "product_reviews",
        [
            _pr("R1", "2026-06-10 08:30:00"),
            _pr("R2", "2026-06-15 09:00:00"),
            _pr("R3", "2026-06-20 23:00:00"),  # 當日有時間分量（驗上界含當日）
            _pr("R4", "2026-07-05 00:00:00"),  # 隔月·應被日期區間排除
        ],
    )
    db.replace_source_findings(
        "product_reviews", "R1", [_finding("R1", "negative", "content", "商品內容")]
    )
    db.replace_source_findings("product_reviews", "R2", [_finding("R2", "positive")])
    db.replace_source_findings(
        "product_reviews", "R3", [_finding("R3", "negative", "supplier", "供應商履約", 0.6)]
    )
    db.replace_source_findings(
        "product_reviews", "R4", [_finding("R4", "negative", "content", "商品內容")]
    )


def test_attribution_overview_kpi_and_distributions(temp_db) -> None:
    """6 月區間：total_intake/judged/attributed KPI + 傾向 / L1 分布正確（R4 因日期排除）。"""
    _seed(temp_db)
    ov = db.attribution_overview(
        source="product_reviews", date_from="2026-06-01", date_to="2026-06-30"
    )
    assert ov["total_intake"] == 3  # R1/R2/R3（R4 隔月排除）
    assert ov["judged"] == 3  # 皆有 finding
    assert ov["attributed"] == 2  # R1 content + R3 supplier（R2 正向無 l1）
    by_pol = {r["polarity"]: r["n"] for r in ov["by_polarity"]}
    assert by_pol["negative"] == 2 and by_pol["positive"] == 1
    by_l1 = {r["code"]: r["n"] for r in ov["by_l1"]}
    assert by_l1 == {"content": 1, "supplier": 1}


def test_attribution_overview_date_upper_bound_includes_full_day(temp_db) -> None:
    """上界含當日整天：date_to=2026-06-20 仍納入 R3（'…20 23:00'），排除隔月 R4（Phase 1 sargable 語義）。"""
    _seed(temp_db)
    ov = db.attribution_overview(
        source="product_reviews", date_from="2026-06-20", date_to="2026-06-20"
    )
    assert ov["total_intake"] == 1  # 僅 R3（當日有時間分量仍入選）
    assert ov["attributed"] == 1
    assert {r["code"] for r in ov["by_l1"]} == {"supplier"}


def test_attribution_overview_model_filter_source_branch(temp_db) -> None:
    """model 篩選（source branch）：只計所選模型的判決級指標；total_intake 不受影響（進線語義）。"""
    _seed(temp_db)
    # R1/R2 用預設空 model；改 R3 為另一模型（重判快照語義：judgments.model=當前判決模型）
    db.replace_source_findings(
        "product_reviews",
        "R3",
        [
            TicketFinding(
                finding_id="fd_product_reviews_R3__supplier",
                ticket_id="R3",
                recommended_action="no_action",
                polarity="negative",
                l1_domain_code="supplier",
                l1_label="供應商履約",
                confidence=0.6,
                raw_confidence=0.6,
                confidence_tier="auto_accept",
                judgment_stage="judged",
                model_used="seed-2-0-lite",
            )
        ],
    )
    ov = db.attribution_overview(source="product_reviews", model=["seed-2-0-lite"])
    assert ov["total_intake"] == 4  # 進線數不受 model 篩選影響
    assert ov["judged"] == 1 and ov["attributed"] == 1  # 僅 R3
    assert {r["code"] for r in ov["by_l1"]} == {"supplier"}


def test_attribution_overview_model_filter_all_sources_branch(temp_db) -> None:
    """model 篩選（縱覽 branch，source=None）：judgments 直接聚合也吃 model 條件。"""
    _seed(temp_db)
    db.replace_source_findings(
        "product_reviews",
        "R1",
        [
            TicketFinding(
                finding_id="fd_product_reviews_R1__content",
                ticket_id="R1",
                recommended_action="no_action",
                polarity="negative",
                l1_domain_code="content",
                l1_label="商品內容",
                confidence=0.9,
                raw_confidence=0.9,
                confidence_tier="auto_accept",
                judgment_stage="judged",
                model_used="gpt-5-mini",
            )
        ],
    )
    ov = db.attribution_overview(source=None, model=["gpt-5-mini"])
    assert ov["judged"] == 1 and ov["attributed"] == 1  # 僅 R1（其餘列 model 為空）
    assert {r["code"] for r in ov["by_l1"]} == {"content"}


def test_attribution_breakdown_model_filter(temp_db) -> None:
    """breakdown 的 model 篩選經 extra 統一套用（L2/L3 兩層一次覆蓋）。"""
    _seed(temp_db)
    ov = db.attribution_breakdown(source="product_reviews", l1="content", model=["nonexistent"])
    assert ov["by_l2"] == [] and ov["by_l3"] == []  # 無該模型判決 → 空分布
