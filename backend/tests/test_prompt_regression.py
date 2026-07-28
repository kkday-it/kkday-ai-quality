"""回歸重跑的逐欄比對邏輯。

這裡不打 LLM——測的是「拿到新輸出之後怎麼算分」，尤其是**沒被人看過的欄不計分**這條：
把 AI 舊判當標準答案會讓分數憑空虛高（當時判錯的欄會被當成正解守住）。
"""

from __future__ import annotations

import pytest

from app.judge import prompt_regression as regression

CASE = {
    "id": 1,
    "conversation": "[USER] 沒選日期就結帳了",
    "ai_output": {
        "L2": "取消政策本身僵化",
        "L3": "規則就是不可退用戶不滿",
        "sentiment": "negative",
        "urgency": 3,
        "confidence": 0.88,
    },
    # 人說：這兩欄改完要變成這樣
    "corrections": {"L2": "商品規格/使用規則事前確認", "sentiment": "neutral"},
    # 人說：這欄本來就對，不准變
    "confirmed": ["urgency"],
}


def test_fixed_when_new_output_matches_the_human_correction() -> None:
    new = {**CASE["ai_output"], "L2": "商品規格/使用規則事前確認", "sentiment": "neutral"}
    out = regression.compare_case(CASE, new)
    assert [f["field"] for f in out["fixed"]] == ["L2", "sentiment"]
    assert out["still_wrong"] == []
    assert [f["field"] for f in out["held"]] == ["urgency"]
    assert out["broken"] == []


def test_still_wrong_carries_expected_and_actual_for_display() -> None:
    """沒修好的欄要能直接顯示「該是什麼 vs 實際是什麼」，不必呼叫端再去挖原案例。"""
    new = {**CASE["ai_output"], "sentiment": "neutral"}  # category 還是舊的錯值
    out = regression.compare_case(CASE, new)
    assert out["still_wrong"] == [
        {
            "field": "L2",
            "expected": "商品規格/使用規則事前確認",
            "actual": "取消政策本身僵化",
        }
    ]
    assert [f["field"] for f in out["fixed"]] == ["sentiment"]


def test_broken_when_a_confirmed_field_changes() -> None:
    """人標過「對」的欄變了＝改壞——這正是回歸存在的理由。"""
    new = {
        **CASE["ai_output"],
        "L2": "商品規格/使用規則事前確認",
        "sentiment": "neutral",
        "urgency": 5,
    }
    out = regression.compare_case(CASE, new)
    assert out["broken"] == [{"field": "urgency", "expected": 3, "actual": 5}]
    assert out["held"] == []


def test_unreviewed_fields_are_not_scored_at_all() -> None:
    """`confidence`／`likely_cause` 人沒看過：新輸出怎麼變都不該進四類任何一類。"""
    new = {
        **CASE["ai_output"],
        "L2": "商品規格/使用規則事前確認",
        "sentiment": "neutral",
        "L3": "完全不同的值",
        "confidence": 0.11,
    }
    out = regression.compare_case(CASE, new)
    scored = {f["field"] for group in out.values() for f in group}
    assert scored == {"L2", "sentiment", "urgency"}
    assert "L3" not in scored and "confidence" not in scored


def test_correction_wins_when_a_field_appears_in_both_lists() -> None:
    """萬一資料髒掉（同欄既在 corrections 又在 confirmed），以「要改成正解」為準，不重複計分。"""
    case = {**CASE, "confirmed": ["urgency", "L2"]}
    new = {**CASE["ai_output"], "L2": "商品規格/使用規則事前確認", "sentiment": "neutral"}
    out = regression.compare_case(case, new)
    assert [f["field"] for f in out["fixed"]] == ["L2", "sentiment"]
    assert [f["field"] for f in out["held"]] == ["urgency"]  # category 不再被當成「要守住」


def test_case_with_nothing_reviewed_scores_nothing() -> None:
    """全未評判的案例不會影響回歸分數（也不該讓它看起來像通過）。"""
    out = regression.compare_case(
        {"id": 2, "ai_output": {"L2": "x"}, "corrections": {}, "confirmed": []},
        {"L2": "y"},
    )
    assert out == {"fixed": [], "still_wrong": [], "broken": [], "held": []}


def test_start_refuses_empty_and_oversized_batches() -> None:
    """回歸是「當場看結果」的路徑，超量該走正式跑批。"""
    with pytest.raises(ValueError, match="沒有可回歸的案例"):
        regression.start([], "prompt", {})
    too_many = [dict(CASE) for _ in range(regression.MAX_CASES + 1)]
    with pytest.raises(ValueError, match=f"上限 {regression.MAX_CASES}"):
        regression.start(too_many, "prompt", {})


def test_start_refuses_stub_mode(monkeypatch) -> None:
    """零 token 時拒跑：假結果會讓人以為改寫沒問題，比直接失敗更糟（同 prompt_sandbox 立場）。"""
    monkeypatch.setattr("app.core.settings.resolve_provider_token", lambda _eff: "", raising=True)
    with pytest.raises(ValueError, match="拒絕以假結果執行回歸"):
        regression.start([dict(CASE)], "prompt", {})


def test_get_job_returns_none_for_unknown_id() -> None:
    """行程重啟後舊 job_id 查不到（純記憶體，by design）。"""
    assert regression.get_job("prompt_regression_nope") is None
