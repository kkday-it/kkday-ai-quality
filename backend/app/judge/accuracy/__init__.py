"""初判歸因準確度（package）：label-free 自證 + 真值監督 + 多 model 聯合比較，三路徑一站產出。

原單檔 accuracy.py（526 行）按職責拆為三模組（行為不變、對外 import 面不變）：
- `labelfree`：Cleanlab confident-learning 一致性體檢（無真值時的代理，有循環論證侷限）。
- `supervised`：true_label 監督真準確率（sklearn；優先於 label-free 代理）。
- `ensemble_agreement`：多 voter vs true_label 比較（Cohen's κ + 多數決 vs 最佳單模型）。

對外沿用 `from app.judge import accuracy; accuracy.run()/analyze_supervised(...)`；
`run()` 為三報表的組裝出口（寫 data/reports/accuracy*.{md,json}）。
"""

from __future__ import annotations

import json
from typing import Any

from app.core.paths import REPORTS_DIR as _REPORTS_DIR

from .ensemble_agreement import (
    _write_ensemble_md,
    analyze_model_agreement,
    ensemble_report,
)
from .labelfree import (
    _write_md,
    analyze,
    build_report,
)
from .supervised import (
    _MIN_SUPERVISED as _MIN_SUPERVISED,  # re-export：測試以 accuracy._MIN_SUPERVISED 取門檻
)
from .supervised import (
    _write_supervised_md,
    analyze_supervised,
    supervised_report,
)

__all__ = [
    "analyze",
    "build_report",
    "analyze_supervised",
    "supervised_report",
    "analyze_model_agreement",
    "ensemble_report",
    "run",
]


def run() -> dict[str, Any]:
    """產出 label-free（accuracy.{md,json}）+ 真值監督（accuracy_supervised.{md,json}）
    + 多 model 聯合（accuracy_ensemble.{md,json}）三份報表。

    Returns:
        {label_free, supervised, ensemble} 三報表 dict（供腳本印摘要 / CI 準確度閘門）。真值監督為
        true_label 到位後的**真準確率**，優先於 label-free 自證代理（後者侷限見 labelfree._LIMITATION）。
    """
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    rep = build_report()
    (_REPORTS_DIR / "accuracy.md").write_text(_write_md(rep), encoding="utf-8")
    (_REPORTS_DIR / "accuracy.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    sup = supervised_report()
    (_REPORTS_DIR / "accuracy_supervised.md").write_text(
        _write_supervised_md(sup), encoding="utf-8"
    )
    (_REPORTS_DIR / "accuracy_supervised.json").write_text(
        json.dumps(sup, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    ens = ensemble_report()
    (_REPORTS_DIR / "accuracy_ensemble.md").write_text(_write_ensemble_md(ens), encoding="utf-8")
    (_REPORTS_DIR / "accuracy_ensemble.json").write_text(
        json.dumps(ens, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"label_free": rep, "supervised": sup, "ensemble": ens}
