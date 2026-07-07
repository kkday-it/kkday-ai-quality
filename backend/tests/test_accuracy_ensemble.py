"""多 model 聯合判決比較（accuracy.analyze_model_agreement）純函式測試：合成 preds + true，不碰 DB/LLM。"""

from app.judge import accuracy


def test_majority_rescues_weak_voter():
    """3 voter：a/c 全對、b 把 supplier 全判 content → 多數決救回、ensemble 準確率 1.0；a-c κ=1.0。"""
    true = ["content"] * 20 + ["supplier"] * 20
    preds = {"a": true[:], "b": ["content"] * 40, "c": true[:]}
    r = accuracy.analyze_model_agreement(preds, true)
    assert r["status"] == "ok"
    assert r["n"] == 40
    assert r["per_model_accuracy"] == {"a": 1.0, "b": 0.5, "c": 1.0}
    assert r["ensemble_accuracy"] == 1.0  # 多數決救回 b 的系統性錯
    assert r["best_single"] == {"model": "a", "accuracy": 1.0}
    assert r["ensemble_gain"] == 0.0
    assert len(r["pairwise_kappa"]) == 3
    ac = next(p for p in r["pairwise_kappa"] if {p["a"], p["b"]} == {"a", "c"})
    assert ac["kappa"] == 1.0
    assert r["watch_confusion"] == []  # ensemble 全對 → 無混淆熱點


def test_watch_confusion_when_majority_wrong():
    """多數 voter 把 supplier 判 content → ensemble 判錯 → content↔supplier 熱點被抓出。"""
    true = ["content"] * 20 + ["supplier"] * 20
    preds = {"a": ["content"] * 40, "b": ["content"] * 40, "c": true[:]}
    r = accuracy.analyze_model_agreement(preds, true)
    assert r["ensemble_accuracy"] == 0.5
    assert any(
        w["true"] == "supplier" and w["pred"] == "content" and w["count"] == 20
        for w in r["watch_confusion"]
    )


def test_skipped_too_few_models_or_labels():
    """單 model 或樣本 < 30 → skipped（一致性/準確率不穩不出數）。"""
    assert (
        accuracy.analyze_model_agreement({"a": ["content"] * 40}, ["content"] * 40)["status"]
        == "skipped"
    )
    two_small = {"a": ["content"] * 10, "b": ["content"] * 10}
    assert accuracy.analyze_model_agreement(two_small, ["content"] * 10)["status"] == "skipped"
