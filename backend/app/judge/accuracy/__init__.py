"""初判歸因準確度（package）：label-free 一致性自證（無真值時的代理指標）。

僅 label-free 自證（Cleanlab confident-learning，有循環論證侷限，見 labelfree._LIMITATION）——
線上無人工真值來源，故不做監督式準確率。

對外沿用 `from app.judge import accuracy; accuracy.run()`；`run()` 寫 data/reports/accuracy.{md,json}。
"""

from __future__ import annotations

import json
from typing import Any

from app.core.paths import REPORTS_DIR as _REPORTS_DIR

from .labelfree import (
    _write_md,
    analyze,
    build_report,
)

__all__ = [
    "analyze",
    "build_report",
    "run",
]


def run() -> dict[str, Any]:
    """產出 label-free 一致性報表（data/reports/accuracy.{md,json}）。

    Returns:
        {label_free} 報表 dict（供腳本印摘要）。label-free 為無真值時的自證代理（循環論證侷限見
        labelfree._LIMITATION）——線上無人工真值來源，故不做監督式準確率。
    """
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    rep = build_report()
    (_REPORTS_DIR / "accuracy.md").write_text(_write_md(rep), encoding="utf-8")
    (_REPORTS_DIR / "accuracy.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"label_free": rep}
