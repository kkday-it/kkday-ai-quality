"""真值監督評估（accuracy.analyze_supervised）純陣列測試：正確率計算 + 誤判對 + 樣本不足降級。

不碰 DB（analyze_supervised 為純陣列）；需 scikit-learn（缺則跳過，比照 .[accuracy] 選用依賴慣例）。
"""

from __future__ import annotations

import pytest

pytest.importorskip("sklearn")

from app.judge import accuracy as A  # noqa: E402


def test_supervised_accuracy_and_confusions() -> None:
    """已知 pred/true（30 筆，≥_MIN_SUPERVISED）→ 準確率 = 命中率；最常誤判對正確聚合。"""
    # 30 筆：24 命中（content 15 + supplier 9）、6 誤判（content→supplier）；準確率 24/30 = 0.8
    true = ["content"] * 21 + ["supplier"] * 9
    pred = ["content"] * 15 + ["supplier"] * 6 + ["supplier"] * 9
    rep = A.analyze_supervised(pred, true)
    assert rep["status"] == "ok"
    assert rep["n"] == 30
    assert rep["accuracy"] == pytest.approx(0.8, abs=1e-9)
    # 唯一誤判對：content 被誤判為 supplier 6 次
    assert rep["top_confusions"][0] == {"true": "content", "pred": "supplier", "count": 6}
    assert "content" in rep["per_class"] and "supplier" in rep["per_class"]


def test_supervised_skips_when_insufficient() -> None:
    """樣本 < _MIN_SUPERVISED → skipped（不假精確）。"""
    rep = A.analyze_supervised(["content"] * 5, ["content"] * 5)
    assert rep["status"] == "skipped"
    assert rep["reason"] == "insufficient_labels"


def test_supervised_perfect_accuracy() -> None:
    """全命中 → accuracy 1.0、無誤判對。"""
    n = A._MIN_SUPERVISED
    true = pred = (["content", "supplier", "service"] * n)[:n]
    rep = A.analyze_supervised(pred, true)
    assert rep["status"] == "ok"
    assert rep["accuracy"] == pytest.approx(1.0)
    assert rep["top_confusions"] == []
