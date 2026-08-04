"""評論級歸因歷史（attribution_history）回歸測試（隔離 PostgreSQL 測試庫，合成拋棄列）。

覆蓋事件的寫入與可見性語義：
- kind='prejudge'：replace_source_findings 同交易寫入 + 全欄位嚴格去重
  （同 model+params+結果 → skip；換 model / 改分類 / 改摘要措辭 → 各記一筆）。
- kind='note'：評論級備註 append-only。
- 使用者時間軸的 kind 白名單：內部遙測事件（router_shadow）不得洩漏進 UI。
"""

from __future__ import annotations

from app.core import db
from app.core.schema import TicketFinding
from tests._factories import review_row

_pr_row = review_row


def _finding(
    rec_oid: str,
    domain: str = "content",
    model: str = "gpt-5-mini",
    l1_code: str = "content",
    summary: str = "頁面資訊與現場不符",
) -> TicketFinding:
    """建一筆對應 reviews 列的歸因（可調 model / 分類 / 摘要，供歷史去重比對用例）。"""
    return TicketFinding(
        ticket_id=rec_oid,  # source_id
        recommended_action="no_action",
        l1_domain_code=l1_code,
        l1_label=l1_code,
        polarity="negative",
        sentiment_score=1,
        summary={"zh-tw": summary},
        model_used=model,
    )


def _history(rec_oid: str, kind: str | None = None) -> list[dict]:
    """讀某評論的歸因歷史（可按 kind 過濾；舊到新，最新一筆在最後）。"""
    rows = db.list_attribution_history("reviews", rec_oid)
    return [r for r in rows if kind is None or r["kind"] == kind]


_PARAMS = {"model": "gpt-5-mini", "voter_models": [], "ensemble_sample_rate": 0.0}


def _replace(rec_oid: str, findings: list[TicketFinding], model: str = "gpt-5-mini") -> None:
    """以固定 params 形狀呼叫 replace（模擬 _work_one 的精餾參數快照）。"""
    db.replace_source_findings(
        "reviews",
        rec_oid,
        findings,
        params={**_PARAMS, "model": model},
        job_id="pj_test",
        triggered_by="qa@kkday.com",
    )


# ── kind='prejudge'：寫入 + 全欄位嚴格去重 ─────────────────────────────
def test_first_judgment_records_history(temp_db) -> None:
    """首次初判落一筆 kind='prejudge' 歷史（含 model / 快照 / 觸發資訊）。"""
    db.insert_source_batch("reviews", [_pr_row("H1")])
    _replace("H1", [_finding("H1")])
    events = _history("H1", "prejudge")
    assert len(events) == 1
    e = events[0]
    assert e["model"] == "gpt-5-mini"
    assert e["triggered_by"] == "qa@kkday.com" and e["job_id"] == "pj_test"
    assert len(e["attributions"]) == 1
    assert e["attributions"][0]["l1"]["code"] == "content"


def test_identical_rejudge_skips_history(temp_db) -> None:
    """同 model+參數+結果重新初判兩次 → 去重只留 1 筆（快取命中/零漂移場景不灌水時間軸）。"""
    db.insert_source_batch("reviews", [_pr_row("H2")])
    _replace("H2", [_finding("H2")])
    _replace("H2", [_finding("H2")])
    assert len(_history("H2", "prejudge")) == 1


def test_model_change_records_history(temp_db) -> None:
    """換 model 重新初判（結果相同）→ 記第 2 筆（model 維度為多模型對比關鍵）。"""
    db.insert_source_batch("reviews", [_pr_row("H3")])
    _replace("H3", [_finding("H3")])
    _replace("H3", [_finding("H3", model="gpt-5")], model="gpt-5")
    events = _history("H3", "prejudge")
    assert len(events) == 2
    assert events[-1]["model"] == "gpt-5"  # 舊到新：最新一筆在最後


def test_result_change_records_history(temp_db) -> None:
    """同 model 但分類變化 → 記第 2 筆。"""
    db.insert_source_batch("reviews", [_pr_row("H4")])
    _replace("H4", [_finding("H4")])
    _replace("H4", [_finding("H4", domain="supplier", l1_code="supplier")])
    assert len(_history("H4", "prejudge")) == 2


def test_summary_wording_change_records_history(temp_db) -> None:
    """僅摘要措辭變 → 仍記一筆（全欄位嚴格比對口徑，使用者拍板）。"""
    db.insert_source_batch("reviews", [_pr_row("H5")])
    _replace("H5", [_finding("H5")])
    _replace("H5", [_finding("H5", summary="頁面資訊與現場明顯不符")])
    assert len(_history("H5", "prejudge")) == 2


def test_attribution_count_change_records_history(temp_db) -> None:
    """歸因筆數變化（1→2）→ 記一筆且新快照含 2 筆。"""
    db.insert_source_batch("reviews", [_pr_row("H6")])
    _replace("H6", [_finding("H6")])
    _replace("H6", [_finding("H6"), _finding("H6", domain="supplier", l1_code="supplier")])
    events = _history("H6", "prejudge")
    assert len(events) == 2
    assert len(events[-1]["attributions"]) == 2  # 舊到新：最新一筆在最後


def test_list_prejudge_models_union_and_stub_last(temp_db) -> None:
    """models 清單＝attributions ∪ 歷史快照 distinct；字母序、stub 排最後。"""
    db.insert_source_batch("reviews", [_pr_row("LM1"), _pr_row("LM2")])
    _replace("LM1", [_finding("LM1")])  # gpt-5-mini
    _replace(
        "LM1", [_finding("LM1", model="stub", summary="假判")], model="stub"
    )  # 當前=stub、歷史留 gpt-5-mini
    _replace("LM2", [_finding("LM2", model="a-model")], model="a-model")
    models = db.list_prejudge_models()
    assert models == ["a-model", "gpt-5-mini", "stub"]  # 字母序 + stub 最後（union 含歷史快照）


def test_internal_kinds_excluded_from_user_timeline(temp_db) -> None:
    """內部遙測事件（router_shadow）不得出現在使用者時間軸。

    ⚠️ 這條守的是**白名單本身**：`kind` 是自由 Text 欄，新增內部事件型別不需要 migration，
    所以「忘了排除」是零成本就會發生的事。而前端時間軸對未知 kind 是 v-else 兜底渲染成
    author/content 皆空的灰色「備註」——`failure` 就曾這樣假冒了 390 筆備註。
    改成黑名單或拿掉過濾，本測試會紅。
    """
    from sqlalchemy import insert

    from app.core.db import tables as T

    db.insert_source_batch("reviews", [_pr_row("HK1")])
    _replace("HK1", [_finding("HK1")])
    db.add_history_note("reviews", "HK1", author="qa@kkday.com", content="人工備註")
    with T.get_engine().begin() as c:
        c.execute(
            insert(T.attribution_history).values(
                source="reviews",
                source_id="HK1",
                kind="router_shadow",
                params={"candidates": ["content"], "hit": [], "missed": ["content"]},
            )
        )

    kinds = [e["kind"] for e in db.list_attribution_history("reviews", "HK1")]
    assert "router_shadow" not in kinds, "內部遙測事件洩漏進使用者時間軸"
    assert sorted(set(kinds)) == ["note", "prejudge"]
