"""overview 首頁 AI 法官真實指標（db.ai_judge_overview_stats + /api/overview/ai-judge）測試。

鎖定「縮窄真接」口徑：distinct (source, source_id) 進線去重（1:N 多歸因不重複計）、
created_at 月分組、content 占比計算；空庫優雅回零。需 temp_db（合成拋棄列）。
"""

from __future__ import annotations

from app.core import db
from app.core.schema import TicketFinding


def _finding(sid: str, l1_code: str, created_at: str, suffix: str = "") -> TicketFinding:
    return TicketFinding(
        finding_id=f"fd_product_reviews_{sid}{('__' + suffix) if suffix else ''}",
        ticket_id=sid,
        recommended_action="no_action",
        polarity="negative" if l1_code else "positive",
        l1_domain_code=l1_code,
        l1_label=l1_code,
        confidence=0.9,
        raw_confidence=0.9,
        confidence_tier="auto_accept",
        prejudge_stage="judged",
        created_at=created_at,
    )


def _seed(temp_db) -> None:
    """6 月：A(content)/B(supplier)/C(non_issue)；7 月：D(content)/E(content+supplier 雙歸因)。"""
    db.replace_source_findings(
        "product_reviews", "A", [_finding("A", "content", "2026-06-10T08:00:00", "content")]
    )
    db.replace_source_findings(
        "product_reviews", "B", [_finding("B", "supplier", "2026-06-11T08:00:00", "supplier")]
    )
    db.replace_source_findings("product_reviews", "C", [_finding("C", "", "2026-06-12T08:00:00")])
    db.replace_source_findings(
        "product_reviews", "D", [_finding("D", "content", "2026-07-01T08:00:00", "content")]
    )
    db.replace_source_findings(
        "product_reviews",
        "E",
        [
            _finding("E", "content", "2026-07-02T08:00:00", "content"),
            _finding("E", "supplier", "2026-07-02T08:00:00", "supplier"),
        ],
    )


def test_monthly_ratio_and_distinct_dedup(temp_db) -> None:
    """月分組占比正確；1:N 多歸因（E）在 judged/content 皆只計一次。"""
    _seed(temp_db)
    out = db.ai_judge_overview_stats()
    assert [m["ym"] for m in out["monthly"]] == ["2026-06", "2026-07"]
    m6, m7 = out["monthly"]
    assert m6 == {"ym": "2026-06", "judged": 3, "content": 1, "ratio_pct": 33.33}
    assert m7 == {"ym": "2026-07", "judged": 2, "content": 2, "ratio_pct": 100.0}


def test_totals(temp_db) -> None:
    """totals：進線數 distinct、歸因列數含多歸因、content 占比以進線計。"""
    _seed(temp_db)
    t = db.ai_judge_overview_stats()["totals"]
    assert t["judged_items"] == 5
    assert t["attributed_rows"] == 5  # A/B/D + E×2（C 未歸因不計）
    assert t["content_items"] == 3  # A/D/E
    assert t["content_share_pct"] == 60.0


def test_months_window(temp_db) -> None:
    """months 參數只回最近 N 個月（trend/spark 消費端固定 6 點）。"""
    _seed(temp_db)
    out = db.ai_judge_overview_stats(months=1)
    assert [m["ym"] for m in out["monthly"]] == ["2026-07"]


def test_empty_db_graceful(temp_db) -> None:
    """空庫回零結構（前端 fallback 判斷用），不拋錯不除零。"""
    out = db.ai_judge_overview_stats()
    assert out["monthly"] == []
    assert out["totals"] == {
        "judged_items": 0,
        "attributed_rows": 0,
        "content_items": 0,
        "content_share_pct": 0.0,
    }


def test_endpoint_smoke(temp_db) -> None:
    """/api/overview/ai-judge 端點回同形狀（本地模式無登入系統，不帶 token 直接成功）。"""
    from fastapi.testclient import TestClient

    from app.api.main import app

    _seed(temp_db)
    with TestClient(app) as client:
        r = client.get("/api/overview/ai-judge")
    assert r.status_code == 200
    body = r.json()
    assert body["totals"]["judged_items"] == 5 and len(body["monthly"]) == 2
