"""評論級判決歷史（judgment_history）回歸測試（隔離 PostgreSQL 測試庫，合成拋棄列）。

覆蓋三類事件的寫入語義：
- kind='judgment'：replace_source_findings 同交易寫入 + 全欄位嚴格去重
  （同 model+params+結果 → skip；換 model / 改分類 / 改摘要措辭 → 各記一筆）。
- kind='status'：update/batch_update_finding_status 轉移留痕（同值冪等不記；
  批量按評論聚合、混合現值只記實際轉移）。
- kind='note'：評論級備註 append-only。
"""

from __future__ import annotations

from app.core import db
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


def _finding(
    rec_oid: str,
    domain: str = "content",
    model: str = "gpt-5-mini",
    l1_code: str = "content",
    summary: str = "頁面資訊與現場不符",
) -> TicketFinding:
    """建一筆對應 product_reviews 列的歸因（可調 model / 分類 / 摘要，供歷史去重比對用例）。"""
    return TicketFinding(
        finding_id=f"fd_product_reviews_{rec_oid}__{domain}",
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
    """讀某評論的判決歷史（可按 kind 過濾；新到舊）。"""
    rows = db.list_judgment_history("product_reviews", rec_oid)
    return [r for r in rows if kind is None or r["kind"] == kind]


_PARAMS = {"model": "gpt-5-mini", "voter_models": [], "ensemble_sample_rate": 0.0}


def _replace(rec_oid: str, findings: list[TicketFinding], model: str = "gpt-5-mini") -> None:
    """以固定 params 形狀呼叫 replace（模擬 _work_one 的精餾參數快照）。"""
    db.replace_source_findings(
        "product_reviews",
        rec_oid,
        findings,
        params={**_PARAMS, "model": model},
        job_id="pj_test",
        triggered_by="qa@kkday.com",
    )


# ── kind='judgment'：寫入 + 全欄位嚴格去重 ─────────────────────────────
def test_first_judgment_records_history(temp_db) -> None:
    """首次判決落一筆 kind='judgment' 歷史（含 model / 快照 / 觸發資訊）。"""
    db.insert_source_batch("product_reviews", [_pr_row("H1")])
    _replace("H1", [_finding("H1")])
    events = _history("H1", "judgment")
    assert len(events) == 1
    e = events[0]
    assert e["model"] == "gpt-5-mini"
    assert e["triggered_by"] == "qa@kkday.com" and e["job_id"] == "pj_test"
    assert len(e["attributions"]) == 1
    assert e["attributions"][0]["l1"]["code"] == "content"


def test_identical_rejudge_skips_history(temp_db) -> None:
    """同 model+參數+結果重判兩次 → 去重只留 1 筆（快取命中/零漂移場景不灌水時間軸）。"""
    db.insert_source_batch("product_reviews", [_pr_row("H2")])
    _replace("H2", [_finding("H2")])
    _replace("H2", [_finding("H2")])
    assert len(_history("H2", "judgment")) == 1


def test_model_change_records_history(temp_db) -> None:
    """換 model 重判（結果相同）→ 記第 2 筆（model 維度為多模型對比關鍵）。"""
    db.insert_source_batch("product_reviews", [_pr_row("H3")])
    _replace("H3", [_finding("H3")])
    _replace("H3", [_finding("H3", model="gpt-5")], model="gpt-5")
    events = _history("H3", "judgment")
    assert len(events) == 2
    assert events[0]["model"] == "gpt-5"  # 新到舊


def test_result_change_records_history(temp_db) -> None:
    """同 model 但分類變化 → 記第 2 筆。"""
    db.insert_source_batch("product_reviews", [_pr_row("H4")])
    _replace("H4", [_finding("H4")])
    _replace("H4", [_finding("H4", domain="supplier", l1_code="supplier")])
    assert len(_history("H4", "judgment")) == 2


def test_summary_wording_change_records_history(temp_db) -> None:
    """僅摘要措辭變 → 仍記一筆（全欄位嚴格比對口徑，使用者拍板）。"""
    db.insert_source_batch("product_reviews", [_pr_row("H5")])
    _replace("H5", [_finding("H5")])
    _replace("H5", [_finding("H5", summary="頁面資訊與現場明顯不符")])
    assert len(_history("H5", "judgment")) == 2


def test_attribution_count_change_records_history(temp_db) -> None:
    """歸因筆數變化（1→2）→ 記一筆且新快照含 2 筆。"""
    db.insert_source_batch("product_reviews", [_pr_row("H6")])
    _replace("H6", [_finding("H6")])
    _replace("H6", [_finding("H6"), _finding("H6", domain="supplier", l1_code="supplier")])
    events = _history("H6", "judgment")
    assert len(events) == 2
    assert len(events[0]["attributions"]) == 2


def test_human_review_does_not_pollute_judgment_dedup(temp_db) -> None:
    """人工確認後同結果重判：status 保留但不入判決快照 → 仍去重（不因覆核誤判為結果變化）。"""
    db.insert_source_batch("product_reviews", [_pr_row("H7")])
    _replace("H7", [_finding("H7")])
    db.update_finding_status("fd_product_reviews_H7__content", "confirmed", actor="qa@kkday.com")
    _replace("H7", [_finding("H7")])
    assert len(_history("H7", "judgment")) == 1


# ── kind='status'：覆核轉移留痕 ────────────────────────────────────────
def test_status_transition_recorded(temp_db) -> None:
    """確認 → 撤銷（new）各記一筆 kind='status'（params 含 from/to 與操作者）。"""
    db.insert_source_batch("product_reviews", [_pr_row("S1")])
    fid = "fd_product_reviews_S1__content"
    _replace("S1", [_finding("S1")])
    db.update_finding_status(fid, "confirmed", actor="qa@kkday.com")
    db.update_finding_status(fid, "new", actor="qa@kkday.com")  # 撤銷覆核
    events = _history("S1", "status")
    assert len(events) == 2
    assert events[0]["params"]["to"] == "new"  # 新到舊：撤銷在前
    assert events[0]["params"]["changes"] == [{"finding_id": fid, "from": "confirmed"}]
    assert events[0]["author"] == "qa@kkday.com"


def test_status_same_value_idempotent_no_history(temp_db) -> None:
    """同值重複覆核＝冪等 no-op：不重寫 audit、不記歷史。"""
    db.insert_source_batch("product_reviews", [_pr_row("S2")])
    fid = "fd_product_reviews_S2__content"
    _replace("S2", [_finding("S2")])
    assert db.update_finding_status(fid, "confirmed", actor="qa@kkday.com")
    assert db.update_finding_status(fid, "confirmed", actor="qa@kkday.com")  # 重複點
    assert len(_history("S2", "status")) == 1


def test_batch_status_mixed_current_values(temp_db) -> None:
    """批量覆核混合現值：已是目標狀態者跳過；每評論聚合一筆事件、回報實際更新數。"""
    db.insert_source_batch("product_reviews", [_pr_row("B1"), _pr_row("B2")])
    _replace("B1", [_finding("B1")])
    _replace("B2", [_finding("B2")])
    db.update_finding_status("fd_product_reviews_B1__content", "confirmed", actor="qa@kkday.com")
    r = db.batch_update_finding_status(
        "product_reviews", ["B1", "B2"], "confirmed", actor="qa@kkday.com"
    )
    assert r["updated"] == 1  # B1 已 confirmed 跳過，只更新 B2
    assert r["finding_ids"] == ["fd_product_reviews_B2__content"]
    assert len(_history("B1", "status")) == 1  # 只有最初那筆單筆確認
    assert len(_history("B2", "status")) == 1  # 批量寫入的聚合事件
    assert _history("B2", "status")[0]["params"]["to"] == "confirmed"


# ── kind='note'：評論級備註 ───────────────────────────────────────────
def test_history_note_append_and_order(temp_db) -> None:
    """評論級備註 append-only；時間軸新到舊、與判決事件混排。"""
    db.insert_source_batch("product_reviews", [_pr_row("N1")])
    _replace("N1", [_finding("N1")])
    created = db.add_history_note(
        "product_reviews", "N1", author="qa@kkday.com", content="已與供應商核對"
    )
    assert created["kind"] == "note" and created["id"]
    rows = _history("N1")
    assert [r["kind"] for r in rows] == ["note", "judgment"]  # 新到舊
    assert rows[0]["content"] == "已與供應商核對"


# ── latest_snapshots / list_judgment_models（多模型對比導出）─────────────────
def test_latest_snapshots_takes_newest_per_model(temp_db) -> None:
    """DISTINCT ON 語意鎖定：每評論只取該模型**最新**一筆快照；跨模型互不干擾。"""
    db.insert_source_batch("product_reviews", [_pr_row("LS1")])
    _replace("LS1", [_finding("LS1", summary="第一版")])
    _replace("LS1", [_finding("LS1", summary="第二版")])  # 同模型重判（結果變 → 新快照）
    _replace(
        "LS1", [_finding("LS1", model="seed-2-0-lite", summary="他模型版")], model="seed-2-0-lite"
    )
    snaps = db.latest_snapshots("product_reviews", "gpt-5-mini")
    assert set(snaps) == {"LS1"}
    summary = snaps["LS1"]["attributions"][0]["content"]["summary"]
    assert summary == {"zh-tw": "第二版"}  # 取最新，非第一版
    other = db.latest_snapshots("product_reviews", "seed-2-0-lite")
    assert other["LS1"]["attributions"][0]["content"]["summary"] == {"zh-tw": "他模型版"}
    assert db.latest_snapshots("product_reviews", "nonexistent") == {}


def test_list_judgment_models_union_and_stub_last(temp_db) -> None:
    """models 清單＝judgments ∪ 歷史快照 distinct；字母序、stub 排最後。"""
    db.insert_source_batch("product_reviews", [_pr_row("LM1"), _pr_row("LM2")])
    _replace("LM1", [_finding("LM1")])  # gpt-5-mini
    _replace(
        "LM1", [_finding("LM1", model="stub", summary="假判")], model="stub"
    )  # 當前=stub、歷史留 gpt-5-mini
    _replace("LM2", [_finding("LM2", model="a-model")], model="a-model")
    models = db.list_judgment_models()
    assert models == ["a-model", "gpt-5-mini", "stub"]  # 字母序 + stub 最後（union 含歷史快照）
