#!/usr/bin/env python3
"""Phase D：初判歸因 label-free 準確度報表 wrapper（純讀取，不改資料）。

判準邏輯在 backend/app/judge/accuracy.py（Cleanlab confident-learning，含循環論證侷限聲明）。
本檔僅負責把 backend 掛上 sys.path 後呼叫 accuracy.run() 並印摘要。

用法：python scripts/accuracy_audit.py → 輸出 data/reports/accuracy.{md,json}
     （DB / cleanlab 不可用時優雅降級為 skipped）。
"""

from __future__ import annotations

import sys
from pathlib import Path

# repo 根：scripts/accuracy_audit.py → parents[1]；backend 掛上 sys.path 才能 import app.*
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "backend"))

from app.judge import accuracy  # noqa: E402


def main() -> None:
    """跑報表並印終端摘要。"""
    rep = accuracy.run()
    if rep.get("status") == "ok":
        print(
            f"樣本 {rep['n']} · 類別 {rep['k']} · 一致性代理準確度 {rep['proxy_accuracy']:.1%}"
            f" · 估可疑標註 {rep['est_issue_count']}"
        )
    else:
        print(f"accuracy: skipped — {rep.get('reason', '')}")
    print(f"報表輸出 → {_ROOT / 'data' / 'reports'}")


if __name__ == "__main__":
    main()
