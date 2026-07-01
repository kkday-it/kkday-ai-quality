#!/usr/bin/env python3
"""從 config/ai_judge/rule_C-*.json 的 positive_cases 產 promptfoo 回歸測試集。

每個 L3 的 positive_case（真實評論句）→ 一條測試：期望判到該 L3 code（exact match）。
負向 negative_cases 多含「→ 歸 X」redirect 標註、非純評論文字，不入測試集。
輸出 config/ai_judge/promptfoo/tests.json（promptfooconfig.yaml 引用）。

用法：python scripts/gen_promptfoo_tests.py
"""

from __future__ import annotations

import glob
import json
import os

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_AIJUDGE = os.path.join(_ROOT, "config", "ai_judge")
_OUT = os.path.join(_AIJUDGE, "promptfoo", "tests.json")


def _iter_leaves(node):
    """遞迴取葉節點（無 children，變深度：葉可在 L1/L2/L3）。"""
    kids = node.get("children")
    if not kids:
        yield node
        return
    for k in kids:
        yield from _iter_leaves(k)


def main() -> None:
    """掃全部 rule 檔葉節點的 positive_cases → tests.json（變深度相容）。"""
    tests: list[dict] = []
    for f in sorted(glob.glob(os.path.join(_AIJUDGE, "rule_C-*.json"))):
        data = json.load(open(f, encoding="utf-8"))
        for l1 in data["tree"]:
            for leaf in _iter_leaves(l1):
                code = leaf["code"]
                for pc in leaf.get("positive_cases", []):
                    if not pc or len(pc) < 6:
                        continue
                    tests.append(
                        {
                            "description": f"{code} {leaf['label']}",
                            "vars": {"review": pc},
                            "assert": [{"type": "equals", "value": code}],
                        }
                    )
    os.makedirs(os.path.dirname(_OUT), exist_ok=True)
    json.dump(tests, open(_OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"產出 {len(tests)} 條回歸測試 → {_OUT}")


if __name__ == "__main__":
    main()
