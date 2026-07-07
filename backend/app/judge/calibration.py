"""信心度校準（conf_raw → calibrated）：以人工 true_label 擬合，補回已刪 calibration 表的閉環（G3）。

**為何不用 netcal**：計畫原點名 netcal，但 netcal 1.4 會拉進 torch / gpytorch / pyro / tensorboard
（數百 MB），違背本專案「零重依賴」鐵律。校準一條曲線用不到深度學習框架 → 改用**已在環境內**的
scikit-learn（isotonic / Platt，業界標準、輕量、無 torch），為 `.[accuracy]` extra 選用依賴。

分工（fit 重、apply 輕）：
- **fit**（離線、選用）：讀 judgments 標註列（conf_raw + is_correct，correct＝l1_code==true_label）擬合
  校準器，算 ECE 前後對比，持久化參數。需 sklearn；缺則優雅降級 skipped（比照 accuracy.py 對 cleanlab）。
- **apply**（線上、必用）：讀持久化參數，raw→calibrated 為**純 numpy**（isotonic=np.interp、platt=sigmoid），
  不需 sklearn，任何路徑可用；無擬合參數時回原值（identity，不改變現況）。

線上實際套用（以 calibrated 設 conf_value/tier）與「建議閾值人工閘門後熱更新」屬 Phase 4；本模組
先建立校準引擎 + ECE 驗證。多 worker 參數一致性待 Phase 7（Flagsmith）；現為單機 refit-on-demand。
持久化＝runtime JSON（`data/calibration/calibration.json`，隨 data/ gitignore），非 config（屬派生產物）。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.core.paths import CALIBRATION_DIR as _CALIB_DIR

_log = logging.getLogger(__name__)

# 持久化位置（派生產物，非 config；data/ 已 gitignore）：paths SSOT，勿在此自拼 REPO_ROOT。
_CALIB_FILE = _CALIB_DIR / "calibration.json"

# 校準最少標註樣本：少於此 sklearn 擬合噪音過大、ECE 無意義 → skip（避免假校準）。
# 200 對齊「true_label ≥ 200 才走真值監督」的既有門檻精神，但校準曲線較評估寬鬆，取 50 起步。
_MIN_LABELED = 50
_ECE_BINS = 10


def ece(confidences: Any, correct: Any, bins: int = _ECE_BINS) -> float:
    """Expected Calibration Error：|平均信心 − 實際正確率| 的分箱加權平均（純 numpy，越低越校準）。

    Args:
        confidences: 各樣本信心分數（0–1）。
        correct: 各樣本是否正確（0/1）。
        bins: 等寬分箱數。

    Returns:
        ECE（0＝完美校準）；空輸入回 0.0。
    """
    import numpy as np

    conf = np.asarray(confidences, dtype=float)
    corr = np.asarray(correct, dtype=float)
    n = len(conf)
    if n == 0:
        return 0.0
    edges = np.linspace(0.0, 1.0, bins + 1)
    total = 0.0
    for i in range(bins):
        lo, hi = edges[i], edges[i + 1]
        # 第一箱含左端點，其餘 (lo, hi]，確保每樣本恰落一箱。
        mask = (conf >= lo) & (conf <= hi) if i == 0 else (conf > lo) & (conf <= hi)
        cnt = int(mask.sum())
        if cnt == 0:
            continue
        total += cnt / n * abs(corr[mask].mean() - conf[mask].mean())
    return float(total)


def fit_curve(confidences: Any, correct: Any, method: str = "isotonic") -> dict:
    """擬合校準曲線（純陣列，可離線測試，不碰 DB）。需 sklearn。

    Args:
        confidences: raw 信心分數陣列（0–1）。
        correct: 對應正確標記陣列（0/1）。
        method: 'isotonic'（非參數，通常 ECE 最低）或 'platt'（logistic，小樣本較穩）。

    Returns:
        持久化參數 dict（含 method 與 apply 所需純數值）：
        - isotonic：{method, x:[...], y:[...]}（apply 用 np.interp）。
        - platt：{method, coef, intercept}（apply 用 sigmoid）。

    Raises:
        ValueError: method 不支援。
    """
    import numpy as np

    conf = np.asarray(confidences, dtype=float).reshape(-1)
    corr = np.asarray(correct, dtype=float).reshape(-1)
    if method == "isotonic":
        from sklearn.isotonic import IsotonicRegression

        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(conf, corr)
        # 存斷點（升序）供 apply 以 np.interp 重現，apply 端免依賴 sklearn。
        xs = np.asarray(iso.X_thresholds_, dtype=float)
        ys = np.asarray(iso.y_thresholds_, dtype=float)
        return {"method": "isotonic", "x": xs.tolist(), "y": ys.tolist()}
    if method == "platt":
        from sklearn.linear_model import LogisticRegression

        lr = LogisticRegression().fit(conf.reshape(-1, 1), corr)
        return {
            "method": "platt",
            "coef": float(lr.coef_[0][0]),
            "intercept": float(lr.intercept_[0]),
        }
    raise ValueError(f"未知校準 method：{method}")


def apply_params(raw: float, params: dict | None) -> float:
    """以持久化參數把 raw 信心映射為 calibrated（純 numpy，無 sklearn 依賴）。

    Args:
        raw: 原始信心（0–1）。
        params: fit_curve 產出的參數；None / 空 / 未知 method → 回原值（identity）。

    Returns:
        校準後信心，clip 至 [0, 1]。
    """
    if not params:
        return float(raw)
    import numpy as np

    method = params.get("method")
    if method == "isotonic":
        xs, ys = params.get("x"), params.get("y")
        if not xs or not ys:
            return float(raw)
        return float(np.clip(np.interp(float(raw), xs, ys), 0.0, 1.0))
    if method == "platt":
        z = params["coef"] * float(raw) + params["intercept"]
        return float(np.clip(1.0 / (1.0 + np.exp(-z)), 0.0, 1.0))
    return float(raw)


def load_params() -> dict | None:
    """讀持久化校準參數；檔不存在 / 壞檔 → None（apply 回 identity）。"""
    try:
        return json.loads(_CALIB_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def apply_calibration(raw: float) -> float:
    """線上套用：raw → calibrated（載入持久化參數；無擬合則 identity）。純 numpy，任何路徑可用。"""
    return apply_params(raw, load_params())


def _load_labeled() -> tuple[list[float], list[int]] | None:
    """撈 judgments 標註列 → (conf_raw 清單, is_correct 清單)。

    correct＝預測 l1_code 命中人工真值 true_label（皆非空）；DB 不可達回 None。校準對象＝L1 域歸因
    是否正確（true_label 語義＝正確 L1 域 code，見 findings.update_finding_true_label）。
    """
    try:
        from sqlalchemy import select

        from app.core.db import tables as T

        jg = T.judgments
        stmt = select(jg.c.conf_raw, jg.c.l1_code, jg.c.true_label).where(
            jg.c.conf_raw.isnot(None),
            jg.c.true_label.isnot(None),
            jg.c.true_label != "",
            jg.c.l1_code.isnot(None),
            jg.c.l1_code != "",
        )
        with T.get_engine().connect() as c:
            rows = c.execute(stmt).all()
    except Exception as e:  # noqa: BLE001  DB 未就緒 / 表缺 → 交由呼叫端標 skipped
        _log.warning("校準載入標註列失敗：%s", str(e).splitlines()[0][:160])
        return None
    conf = [float(r.conf_raw) for r in rows]
    correct = [1 if r.l1_code == r.true_label else 0 for r in rows]
    return conf, correct


def fit_and_persist(method: str = "isotonic") -> dict:
    """離線擬合 + 持久化：讀標註列 → 擬合校準器 → 算 ECE 前後 → 寫參數檔。優雅降級（不拋）。

    Args:
        method: 'isotonic' | 'platt'。

    Returns:
        報表 dict：{status: fitted|skipped, reason?, method, n, n_correct, ece_before, ece_after,
        improved, fitted_at}。status=skipped 時不寫檔（保留既有參數）。
    """
    data = _load_labeled()
    if data is None:
        return {"status": "skipped", "reason": "db_unavailable"}
    conf, correct = data
    n = len(conf)
    if n < _MIN_LABELED:
        return {"status": "skipped", "reason": "insufficient_labels", "n": n, "min": _MIN_LABELED}
    n_correct = int(sum(correct))
    if n_correct == 0 or n_correct == n:
        # 全對 / 全錯 → 無正負對比，校準器退化；skip 避免病態擬合。
        return {"status": "skipped", "reason": "single_class", "n": n, "n_correct": n_correct}
    try:
        params = fit_curve(conf, correct, method)
    except ImportError:
        return {"status": "skipped", "reason": "sklearn_unavailable", "n": n}
    ece_before = ece(conf, correct)
    calibrated = [apply_params(x, params) for x in conf]
    ece_after = ece(calibrated, correct)
    params["fitted_at"] = datetime.now(timezone.utc).isoformat()
    params["n"] = n
    _CALIB_DIR.mkdir(parents=True, exist_ok=True)
    _CALIB_FILE.write_text(json.dumps(params, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "status": "fitted",
        "method": method,
        "n": n,
        "n_correct": n_correct,
        "ece_before": round(ece_before, 4),
        "ece_after": round(ece_after, 4),
        "improved": ece_after < ece_before,
        "fitted_at": params["fitted_at"],
    }
