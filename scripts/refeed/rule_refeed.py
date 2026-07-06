#!/usr/bin/env python3
"""rule 反哺飛輪 wrapper：挑邊界誤判候選（預設）/ 精煉某 node canon 寫回 DB active 版（--apply）。

判準邏輯在 backend/app/judge/rule_refeed.py（find_boundary_cases / refeed_node_canon）。本檔掛 backend
上 sys.path 後委派 + 撈 DB 資料。

用法：
  python scripts/refeed/rule_refeed.py                                  # 印反哺候選（ensemble 判錯域對 + 例句）
  python scripts/refeed/rule_refeed.py --apply C-1 C-1-1-4 "精煉後 canon"  # 寫回該 node 的 canon（熱重載即生效）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# repo 根：scripts/refeed/rule_refeed.py → parents[2]；backend 掛上 sys.path 才能 import app.*
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "backend"))

from app.judge import rule_refeed  # noqa: E402


def _load_rows() -> list[dict]:
    """撈 judgments 有 model_votes + true_label 的列 → [{pred, true, evidence}]（pred＝聯合判決 l1_code）。"""
    from sqlalchemy import select

    from app.core.db import tables as T

    jg = T.judgments
    stmt = select(jg.c.l1_code, jg.c.true_label, jg.c.evidence).where(
        jg.c.model_votes.isnot(None),
        jg.c.true_label.isnot(None),
        jg.c.true_label != "",
        jg.c.l1_code.isnot(None),
        jg.c.l1_code != "",
    )
    with T.get_engine().connect() as c:
        return [{"pred": r.l1_code, "true": r.true_label, "evidence": r.evidence or ""} for r in c.execute(stmt)]


def _report() -> None:
    """印反哺候選：ensemble 判錯的 (true→pred) 域對 + 佐證例句（content↔supplier 優先）。"""
    try:
        rows = _load_rows()
    except Exception as e:  # noqa: BLE001  DB 不可達 → 優雅提示
        print(f"DB 不可達（先起後端 / 確認已跑 ensemble 並標 true_label）：{e}")
        return
    cands = rule_refeed.find_boundary_cases(rows)
    if not cands:
        print("無反哺候選（尚無 ensemble model_votes + true_label 的誤判資料）")
        return
    print(f"反哺候選（{len(cands)} 個域對；⚠️＝content↔supplier 重點監看，優先精煉）：")
    for c in cands:
        mark = "⚠️" if c["watch"] else "  "
        print(f"{mark} {c['true']} 被誤判為 {c['pred']} × {c['count']}")
        for ex in c["examples"][:3]:
            print(f"     例：{ex[:60]}")
    print("\n精煉後寫回：python scripts/refeed/rule_refeed.py --apply <RULE_CODE> <NODE_CODE> \"<新 canon>\"")


def main() -> None:
    ap = argparse.ArgumentParser(description="rule 反哺飛輪：挑候選 / 精煉 canon 寫回")
    ap.add_argument("--apply", nargs=3, metavar=("RULE_CODE", "NODE_CODE", "CANON"), help="寫回某 node 精煉後的 canon")
    ap.add_argument("--user", default="", help="寫入作者（存 audit）")
    args = ap.parse_args()
    if args.apply:
        rc, nc, canon = args.apply
        res = rule_refeed.refeed_node_canon(rc, nc, canon, note="CLI 反哺精煉", author=args.user)
        print(f"反哺{'成功（熱重載已生效）' if res['updated'] else '未命中節點（檢查 NODE_CODE）'}：{res}")
    else:
        _report()


if __name__ == "__main__":
    main()
