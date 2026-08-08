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
        ticket_id=rec_oid,
        recommended_action="no_action",
        polarity=polarity,
        l1_domain_code=l1_code,
        l1_label=l1_label,
        confidence=conf,
        raw_confidence=conf,
        confidence_tier="auto_accept",
        prejudge_stage="judged",
    )


def _seed(temp_db) -> None:
    """R1 負向content / R2 正向未歸因 / R3 負向supplier（皆 6 月）+ R4 負向content（7 月·出區間）。"""
    db.insert_source_batch(
        "reviews",
        [
            _pr("R1", "2026-06-10 08:30:00"),
            _pr("R2", "2026-06-15 09:00:00"),
            _pr("R3", "2026-06-20 23:00:00"),  # 當日有時間分量（驗上界含當日）
            _pr("R4", "2026-07-05 00:00:00"),  # 隔月·應被日期區間排除
        ],
    )
    db.replace_source_findings("reviews", "R1", [_finding("R1", "negative", "content", "商品內容")])
    db.replace_source_findings("reviews", "R2", [_finding("R2", "positive")])
    db.replace_source_findings(
        "reviews", "R3", [_finding("R3", "negative", "supplier", "供應商履約", 0.6)]
    )
    db.replace_source_findings("reviews", "R4", [_finding("R4", "negative", "content", "商品內容")])


def test_attribution_overview_kpi_and_distributions(temp_db) -> None:
    """6 月區間：total_intake/judged/attributed KPI + 傾向 / L1 分布正確（R4 因日期排除）。"""
    _seed(temp_db)
    ov = db.attribution_overview(source="reviews", date_from="2026-06-01", date_to="2026-06-30")
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
    ov = db.attribution_overview(source="reviews", date_from="2026-06-20", date_to="2026-06-20")
    assert ov["total_intake"] == 1  # 僅 R3（當日有時間分量仍入選）
    assert ov["attributed"] == 1
    assert {r["code"] for r in ov["by_l1"]} == {"supplier"}


def test_attribution_overview_model_filter_source_branch(temp_db) -> None:
    """model 篩選（source branch）：只計所選模型的初判級指標；total_intake 不受影響（進線語義）。"""
    _seed(temp_db)
    # R1/R2 用預設空 model；改 R3 為另一模型（重新初判快照語義：attributions.model=當前初判模型）
    db.replace_source_findings(
        "reviews",
        "R3",
        [
            TicketFinding(
                ticket_id="R3",
                recommended_action="no_action",
                polarity="negative",
                l1_domain_code="supplier",
                l1_label="供應商履約",
                confidence=0.6,
                raw_confidence=0.6,
                confidence_tier="auto_accept",
                prejudge_stage="judged",
                model_used="seed-2-0-lite",
            )
        ],
    )
    ov = db.attribution_overview(source="reviews", model=["seed-2-0-lite"])
    assert ov["total_intake"] == 4  # 進線數不受 model 篩選影響
    assert ov["judged"] == 1 and ov["attributed"] == 1  # 僅 R3
    assert {r["code"] for r in ov["by_l1"]} == {"supplier"}


def test_attribution_overview_model_filter_all_sources_branch(temp_db) -> None:
    """model 篩選（縱覽 branch，source=None）：attributions 直接聚合也吃 model 條件。"""
    _seed(temp_db)
    db.replace_source_findings(
        "reviews",
        "R1",
        [
            TicketFinding(
                ticket_id="R1",
                recommended_action="no_action",
                polarity="negative",
                l1_domain_code="content",
                l1_label="商品內容",
                confidence=0.9,
                raw_confidence=0.9,
                confidence_tier="auto_accept",
                prejudge_stage="judged",
                model_used="gpt-5-mini",
            )
        ],
    )
    ov = db.attribution_overview(source=None, model=["gpt-5-mini"])
    assert ov["judged"] == 1 and ov["attributed"] == 1  # 僅 R1（其餘列 model 為空）
    assert {r["code"] for r in ov["by_l1"]} == {"content"}


def test_attribution_breakdown_model_filter(temp_db) -> None:
    """breakdown 的 model 篩選經 extra 統一套用。"""
    _seed(temp_db)
    ov = db.attribution_breakdown(source="reviews", l1="content", model=["nonexistent"])
    assert ov["by_l2"] == []  # 無該模型初判 → 空分布


def test_by_tier_counts_human_rows_in_own_bucket(temp_db) -> None:
    """人工糾正過的列必須進 `by_tier` 的 human 桶，**不能從分佈裡消失**。

    這支鎖的是一個曾經靜默存在的缺陷：糾正會把 `conf_value` 設為 NULL（原 AI 信心描述的是舊分類，
    掛在新分類上是謊言），只留 `conf_tier='human'`；但 `_by_tier` 當時只做數值分箱、其查詢又帶
    `WHERE conf_value IS NOT NULL`——於是人工列被整批濾掉，分佈總數悄悄變小，**不報任何錯**。

    ⚠️ **斷言的是結果不是機制**：`test_corrections.py` 已經有一支斷言「糾正後 conf_tier == 'human'」，
    但那只驗到寫入端。寫入端做對了、聚合端不認得它，照樣是壞的——這正是當初漏掉的那一格。
    改動任何吃 `conf_value` 的聚合時，這支會紅。
    """
    from sqlalchemy import update

    from app.core.db import tables as T

    _seed(temp_db)
    jg = T.attributions
    before = db.attribution_overview(source="reviews")["by_tier"]
    total_before = sum(before.values())
    assert before.get("human", 0) == 0, "夾具不該預先有人工列"
    assert total_before > 0, "夾具至少要有一列有信心值，否則本測試驗不到東西"

    # 把一列改成人工列的形狀（與 corrections.correct_attribution 的寫入一致）
    with T.get_engine().begin() as c:
        oid = c.execute(jg.select().with_only_columns(jg.c.attribution_oid).limit(1)).scalar_one()
        c.execute(
            update(jg)
            .where(jg.c.attribution_oid == oid)
            .values(conf_value=None, conf_raw=None, conf_tier="human")
        )

    after = db.attribution_overview(source="reviews")["by_tier"]
    assert after.get("human") == 1, "人工列必須出現在 human 桶"
    assert sum(after.values()) == total_before, (
        "分佈總數不能因為某列變成人工列而減少——少一列就是靜默漏計"
    )
