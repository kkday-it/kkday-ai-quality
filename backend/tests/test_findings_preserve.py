"""重判（replace_source_findings）人工覆核軸保留 + 日期上界含當日整天回歸測試。

需 temp_db fixture（隔離 PostgreSQL 測試庫，合成拋棄列，非真實資料）：
- G2：重判整組替換舊列時，人工既定 status（confirmed/dismissed）必須依 finding_id 保留，
  不得被打回初始 "new" 洗掉人工覆核結果。
- 效能改動語義守恆：date_to 上界改半開 `< date_to||'~'` 後，仍須含當日「有時間分量」的列
  （naive `<= date_to` 會漏），且排除隔日。
"""

from __future__ import annotations

from sqlalchemy import select

from app.core import db
from app.core.db import tables as T
from app.core.schema import TicketFinding


def _pr_row(rec_oid: str, **overrides) -> dict:
    """建一筆最小 product_reviews 源列（源欄名、值皆 Text）。"""
    base = {
        "rec_oid": rec_oid,
        "create_date": "2026-06-01 10:00:00",
        "rec_desc": "內容",
        "rec_scores": "5",
        "prod_oid": "P1",
        "order_snap_json": "{}",
    }
    base.update(overrides)
    return base


def _finding(rec_oid: str, domain: str = "content", status: str = "new") -> TicketFinding:
    """建一筆對應 product_reviews 列的歸因（finding_id 依 fd_{source}_{source_id}__{domain} 慣例）。"""
    return TicketFinding(
        finding_id=f"fd_product_reviews_{rec_oid}__{domain}",
        ticket_id=rec_oid,  # source_id
        recommended_action="no_action",
        status=status,
    )


def _status_of(finding_id: str) -> str | None:
    """讀某 finding 的 status。"""
    jg = T.judgments
    with T.get_engine().connect() as c:
        r = c.execute(select(jg.c.status).where(jg.c.finding_id == finding_id)).first()
    return r.status if r else None


def test_rejudge_preserves_human_status(temp_db) -> None:
    """人工覆核（confirmed）後重判：status 依 finding_id 保留（G2），不被新判決 new 洗掉。"""
    db.insert_source_batch("product_reviews", [_pr_row("R1")])
    fid = "fd_product_reviews_R1__content"
    db.replace_source_findings("product_reviews", "R1", [_finding("R1")])
    db.update_finding_status(fid, "confirmed")
    db.replace_source_findings("product_reviews", "R1", [_finding("R1", status="new")])
    assert _status_of(fid) == "confirmed"


def test_rejudge_preserves_dismissed_status(temp_db) -> None:
    """人工 dismissed 後重判仍保留。"""
    db.insert_source_batch("product_reviews", [_pr_row("R2")])
    fid = "fd_product_reviews_R2__content"
    db.replace_source_findings("product_reviews", "R2", [_finding("R2")])
    db.update_finding_status(fid, "dismissed")
    db.replace_source_findings("product_reviews", "R2", [_finding("R2", status="new")])
    assert _status_of(fid) == "dismissed"


def test_rejudge_untouched_status_follows_new_judgment(temp_db) -> None:
    """從未覆核（仍為 new）者重判：status 由新判決決定（不硬回填 new，為 Phase 4 自動確認預留）。"""
    db.insert_source_batch("product_reviews", [_pr_row("R3")])
    fid = "fd_product_reviews_R3__content"
    db.replace_source_findings("product_reviews", "R3", [_finding("R3", status="new")])
    db.replace_source_findings("product_reviews", "R3", [_finding("R3", status="new")])
    assert _status_of(fid) == "new"


def test_date_to_includes_same_day_with_time_component(temp_db) -> None:
    """date_to 上界含當日整天：'2026-06-30 23:00' 應入選、隔日 '2026-07-01 00:00' 應排除。"""
    db.insert_source_batch(
        "product_reviews",
        [
            _pr_row("D1", create_date="2026-06-30 23:00:00"),
            _pr_row("D2", create_date="2026-07-01 00:00:00"),
        ],
    )
    result = db.list_problems(
        source="product_reviews", date_from="2026-06-01", date_to="2026-06-30"
    )
    assert result["total"] == 1
    assert result["rows"][0]["_group"] == "D1"


def test_rejudge_does_not_preserve_auto_confirmed(temp_db) -> None:
    """G1 auto_confirmed（系統自動確認·非人工）重判不保留 → 由新判決 status 決定（有別於人工 confirmed）。"""
    db.insert_source_batch("product_reviews", [_pr_row("R4")])
    fid = "fd_product_reviews_R4__content"
    db.replace_source_findings("product_reviews", "R4", [_finding("R4")])
    db.update_finding_status(fid, "auto_confirmed")  # 系統自動確認
    db.replace_source_findings("product_reviews", "R4", [_finding("R4", status="new")])
    assert _status_of(fid) == "new"  # auto_confirmed 未被保留（與 confirmed 對比）
