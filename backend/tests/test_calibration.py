"""信心度校準（judge/calibration）純陣列測試：ECE 前後下降 + apply 純 numpy 重現 + 邊界。

不碰 DB（fit_curve/apply_params 為純陣列核心）；需 scikit-learn（缺則整檔 skip，比照選用依賴慣例）。
"""

from __future__ import annotations

import pytest

pytest.importorskip("sklearn")  # 校準 fit 需 sklearn；未裝 .[accuracy] 則跳過

from app.judge import calibration as C  # noqa: E402


def _synthetic(n: int = 800):
    """合成過度自信的 (raw_conf, correct)：真實正確率 true_p，raw 系統性推高 → ECE 大。"""
    import numpy as np

    rng = np.random.default_rng(0)
    true_p = rng.uniform(0.05, 0.95, n)
    correct = (rng.uniform(size=n) < true_p).astype(int)
    raw = np.clip(true_p * 0.5 + 0.5, 0.0, 1.0)  # 過度自信
    return raw, correct


@pytest.mark.parametrize("method", ["isotonic", "platt"])
def test_calibration_reduces_ece(method: str) -> None:
    """擬合後 ECE 明顯低於原始（校準有效的核心驗證）。"""
    raw, correct = _synthetic()
    ece_before = C.ece(raw, correct)
    params = C.fit_curve(raw, correct, method)
    calibrated = [C.apply_params(x, params) for x in raw]
    ece_after = C.ece(calibrated, correct)
    assert ece_before > 0.1  # 合成資料確實 miscalibrated
    assert ece_after < ece_before  # 校準確有改善
    assert params["method"] == method


def test_apply_params_matches_sklearn_predict() -> None:
    """apply_params（純 numpy）重現 sklearn isotonic 預測（apply 端免依賴 sklearn 的正確性保證）。"""
    import numpy as np
    from sklearn.isotonic import IsotonicRegression

    raw, correct = _synthetic(400)
    params = C.fit_curve(raw, correct, "isotonic")
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(raw, correct)
    for x in (0.0, 0.3, 0.55, 0.9, 1.0):
        assert C.apply_params(x, params) == pytest.approx(float(iso.predict([x])[0]), abs=1e-6)
    _ = np  # 明示使用


def test_apply_params_identity_when_no_params() -> None:
    """無擬合參數（None/空/未知 method）→ 回原值，不改變現況（線上安全預設）。"""
    assert C.apply_params(0.42, None) == 0.42
    assert C.apply_params(0.42, {}) == 0.42
    assert C.apply_params(0.42, {"method": "unknown"}) == 0.42


def test_ece_empty_and_perfect() -> None:
    """ECE 邊界：空輸入回 0；完美校準（信心＝實際率）ECE 為 0。"""
    assert C.ece([], []) == 0.0
    # 兩箱各半：信心 0.0 全錯、信心 1.0 全對 → 完美校準
    assert C.ece([0.0, 0.0, 1.0, 1.0], [0, 0, 1, 1]) == pytest.approx(0.0, abs=1e-9)
