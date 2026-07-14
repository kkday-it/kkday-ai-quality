"""B1 按條件篩選 × 單一 prompt 測試（prompt_eval.py 的 ID 抽樣路徑）。

覆蓋：
- `sample_domain_by_ids`/`sample_polarity_by_ids`：依指定 id 清單取樣（非 md5 全表抽樣），
  忠實反映使用者在歸因列表上選的子集，不做 pos/neg 或三態平衡。
- `run_eval(..., filter_ids=...)`：給定時樣本改走 ID 抽樣、`result["filtered"]=True`；
  未給時沿用既有 md5 抽樣、`filtered=False`（預設行為不變，回歸鎖定）。

用真實 PostgreSQL 測試庫（temp_db）：需要 judgments 列有 l1_code/l2_code/is_primary 等實欄，
純 monkeypatch 不足以覆蓋 SQL 篩選正確性本身。
"""

from __future__ import annotations

from app.core import db
from app.core.schema import TicketFinding
from app.judge import prompt_eval as pe


def _pr_row(rec_oid: str) -> dict:
    return {
        "rec_oid": rec_oid,
        "create_date": "2026-07-01 08:30:00",
        "rec_title": "評論標題",
        "rec_desc": f"評論內容 {rec_oid}",
        "rec_scores": "1",
        "prod_oid": "P1",
        "order_snap_json": "{}",
    }


def _finding(
    rec_oid: str,
    *,
    l1_code: str,
    l1_label: str,
    l2_code: str,
    l2_label: str,
    polarity: str = "negative",
    is_primary: bool = True,
    sentiment: int = 1,
) -> TicketFinding:
    return TicketFinding(
        finding_id=f"fd_product_reviews_{rec_oid}__{l1_code}",
        ticket_id=rec_oid,
        recommended_action="no_action",
        polarity=polarity,
        sentiment_score=sentiment,
        l1_domain_code=l1_code,
        l1_label=l1_label,
        l2_code=l2_code,
        l2_label=l2_label,
        confidence=0.9,
        raw_confidence=0.9,
        confidence_tier="auto_accept",
        judgment_stage="judged",
        is_primary=is_primary,
        summary={"zh-tw": "測試摘要"},
        model_used="gpt-5-mini",
    )


def test_sample_domain_by_ids_returns_only_requested_ids(temp_db) -> None:
    """僅回指定 id 清單內、且該域確實有歸因的列；不做 pos/neg 平衡（樣本即篩選結果本身）。"""
    db.insert_source_batch("product_reviews", [_pr_row("F1"), _pr_row("F2"), _pr_row("F3")])
    db.replace_source_findings(
        "product_reviews",
        "F1",
        [
            _finding(
                "F1",
                l1_code="supplier",
                l1_label="供應商履約",
                l2_code="C-3-1",
                l2_label="人員服務",
            )
        ],
    )
    db.replace_source_findings(
        "product_reviews",
        "F2",
        [
            _finding(
                "F2", l1_code="content", l1_label="商品內容", l2_code="C-1-2", l2_label="行程資訊"
            )
        ],
    )
    db.replace_source_findings(
        "product_reviews",
        "F3",
        [
            _finding(
                "F3",
                l1_code="supplier",
                l1_label="供應商履約",
                l2_code="C-3-2",
                l2_label="駕駛接送",
            )
        ],
    )

    # 只選 F1/F3（模擬歸因列表篩選 taxonomy=supplier 後的子集），F2 不在範圍內
    out = pe.sample_domain_by_ids("supplier", ["F1", "F3"])
    ids = {r["id"] for r in out}
    assert ids == {"F1", "F3"}  # F2 不在請求的 id 清單內，即使它也存在 judgments
    by_id = {r["id"]: r for r in out}
    assert by_id["F1"]["ref_l2s"] == ["C-3-1"]
    assert by_id["F1"]["ref_primary"] == "C-3-1"
    assert by_id["F3"]["ref_l2s"] == ["C-3-2"]


def test_sample_domain_by_ids_empty_list_returns_empty(temp_db) -> None:
    """空 id 清單直接回空（不誤觸發全表查詢）。"""
    assert pe.sample_domain_by_ids("supplier", []) == []


def test_sample_polarity_by_ids_returns_only_requested_ids(temp_db) -> None:
    """極性參照集：僅回指定 id 清單內、有 sentiment_score 的列。"""
    db.insert_source_batch("product_reviews", [_pr_row("P1"), _pr_row("P2")])
    db.replace_source_findings(
        "product_reviews",
        "P1",
        [
            _finding(
                "P1",
                l1_code="content",
                l1_label="商品內容",
                l2_code="C-1-1",
                l2_label="定位",
                polarity="negative",
                sentiment=1,
            )
        ],
    )
    db.replace_source_findings(
        "product_reviews",
        "P2",
        [
            _finding(
                "P2",
                l1_code="",
                l1_label="",
                l2_code="",
                l2_label="",
                polarity="positive",
                sentiment=5,
            )
        ],
    )
    out = pe.sample_polarity_by_ids(["P1", "P2"])
    by_id = {r["id"]: r for r in out}
    assert by_id["P1"]["polarity"] == "negative" and by_id["P1"]["sentiment"] == 1
    assert by_id["P2"]["polarity"] == "positive" and by_id["P2"]["sentiment"] == 5


def test_run_eval_filter_ids_marks_result_filtered(monkeypatch) -> None:
    """filter_ids 給定 → 走 ID 抽樣路徑、result['filtered']=True；未給 → 沿用 md5 抽樣、filtered=False。"""
    from app.judge.llm import client

    monkeypatch.setattr(client, "is_stub", lambda: False)
    monkeypatch.setattr(pe, "domain_of", lambda arg: "supplier")

    called_with_ids: list[list[str]] = []

    def _fake_sample_by_ids(dom, ids):
        called_with_ids.append(list(ids))
        return []

    called_md5 = []

    def _fake_sample_domain(dom, n):
        called_md5.append(n)
        return []

    monkeypatch.setattr(pe, "sample_domain_by_ids", _fake_sample_by_ids)
    monkeypatch.setattr(pe, "sample_domain", _fake_sample_domain)
    monkeypatch.setattr(pe, "_run_domain", lambda pid, dom_code, samples, **k: {"prompt": dom_code})

    out = pe.run_eval("C-3", 10, filter_ids=["A", "B", "C"])
    assert out["filtered"] is True
    assert called_with_ids == [["A", "B", "C"]]
    assert called_md5 == []  # 未落到 md5 抽樣路徑

    called_with_ids.clear()
    out2 = pe.run_eval("C-3", 10, filter_ids=None)
    assert out2["filtered"] is False
    assert called_md5 == [10]
    assert called_with_ids == []


def test_run_eval_filter_ids_truncated_to_n(monkeypatch) -> None:
    """filter_ids 超過 n 筆時截前 n 筆（避免 UI 快測樣本數失控）。"""
    from app.judge.llm import client

    monkeypatch.setattr(client, "is_stub", lambda: False)
    monkeypatch.setattr(pe, "domain_of", lambda arg: "supplier")
    seen = []
    monkeypatch.setattr(pe, "sample_domain_by_ids", lambda dom, ids: seen.append(list(ids)) or [])
    monkeypatch.setattr(pe, "_run_domain", lambda pid, dom_code, samples, **k: {"prompt": dom_code})

    pe.run_eval("C-3", 2, filter_ids=["A", "B", "C", "D"])
    assert seen == [["A", "B"]]
