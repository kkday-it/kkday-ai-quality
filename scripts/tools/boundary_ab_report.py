#!/usr/bin/env python3
"""商品內容嚴格界線 A/B 報告：before（DB 舊規則）vs flat vs cascade，對 silver label 算誤判率。

輸入：
- --eval-set evalset.json：含每筆 group 與 before（舊規則歸因）
- --runs flat.json cascade.json ...：boundary_ab_eval.py run 模式產出（可多個）
- --silver silver.json（選填）：[{id: "<source>|<source_id>", polarity, domains: [l1...], primary}]
  無 silver 時只出「content 歸因率位移」描述性統計。

指標（有 silver 時）：
- content FP 率：預測含 content 但 silver 無 content ÷ silver 無 content 之負向樣本
- content recall：silver 含 content 者被預測含 content 的比例
- primary L1 accuracy + per-class P/R/F1（accuracy.analyze_supervised，sklearn 缺裝時降級 confusion counts）
- polarity 一致率（silver negative 被判非 negative = 漏放行）

用法（backend venv）：
    cd backend
    .venv/bin/python ../scripts/tools/boundary_ab_report.py \
        --eval-set ../tmp/boundary_ab/evalset.json \
        --runs ../tmp/boundary_ab/flat.json ../tmp/boundary_ab/cascade.json \
        --silver ../tmp/boundary_ab/silver.json
"""

import argparse
import json
import os
import sys
from typing import Any

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


def _key(source: str, source_id: str) -> str:
    return f"{source}|{source_id}"


def _pred_of_attrs(attrs: list[dict]) -> dict[str, Any]:
    """歸因清單 → {domains: set, primary: str, negative: bool}。

    計入 negative＋neutral 的歸因列（傾向深化後混合中性評論的問題面向也歸因，
    polarity 欄＝整則傾向）；純 non_issue（無 l1）不計。舊 run（無中性歸因）數字不受影響。
    """
    neg = [a for a in attrs if a.get("polarity") in ("negative", "neutral") and a.get("l1")]
    domains = {a["l1"] for a in neg}
    primary = ""
    if neg:
        prim = [a for a in neg if a.get("primary")]
        primary = (prim[0] if prim else max(neg, key=lambda a: a.get("conf") or 0))["l1"]
    return {"domains": domains, "primary": primary, "negative": bool(neg)}


def _pred_of_before(before: list[dict]) -> dict[str, Any]:
    """evalset before（舊規則 DB 歸因；查詢已限 polarity=negative）→ 同構 pred。"""
    rows = [b for b in before if b.get("l1")]
    domains = {b["l1"] for b in rows}
    primary = ""
    if rows:
        prim = [b for b in rows if b.get("primary")]
        primary = (prim[0] if prim else max(rows, key=lambda b: b.get("conf") or 0))["l1"]
    return {"domains": domains, "primary": primary, "negative": bool(rows)}


def _describe(name: str, preds: dict[str, dict], groups: dict[str, str]) -> dict:
    """無 silver 的描述性統計：content 歸因率（全體 / content 疑似組留存率）。"""
    n = len(preds)
    with_content = [k for k, p in preds.items() if "content" in p["domains"]]
    sus = [k for k, g in groups.items() if g == "content_suspect" and k in preds]
    sus_keep = [k for k in sus if "content" in preds[k]["domains"]]
    return {
        "run": name,
        "n": n,
        "content_rate": round(len(with_content) / n, 3) if n else 0,
        "suspect_n": len(sus),
        "suspect_content_retained": round(len(sus_keep) / len(sus), 3) if sus else 0,
    }


def _score(name: str, preds: dict[str, dict], silver: dict[str, dict]) -> dict:
    """對 silver 打分：content FP/recall、primary accuracy、polarity 一致。"""
    keys = [k for k in silver if k in preds]
    s_neg = [k for k in keys if silver[k].get("polarity") == "negative"]
    # polarity：silver 負向被判非負向 = 漏（不進歸因）
    missed_neg = [k for k in s_neg if not preds[k]["negative"]]
    # content FP/recall（僅 silver 負向樣本上計）
    no_c = [k for k in s_neg if "content" not in (silver[k].get("domains") or [])]
    has_c = [k for k in s_neg if "content" in (silver[k].get("domains") or [])]
    fp = [k for k in no_c if "content" in preds[k]["domains"]]
    tp = [k for k in has_c if "content" in preds[k]["domains"]]
    # primary accuracy（雙方皆有 primary 者）
    pairs = [
        (preds[k]["primary"], silver[k].get("primary") or "")
        for k in s_neg
        if preds[k]["primary"] and silver[k].get("primary")
    ]
    pred_l, true_l = [p for p, _ in pairs], [t for _, t in pairs]
    try:
        from app.judge.accuracy import analyze_supervised

        sup = analyze_supervised(pred_l, true_l)
    except Exception as exc:  # noqa: BLE001  sklearn 未裝等 → 降級手算 accuracy
        acc = sum(1 for p, t in pairs if p == t) / len(pairs) if pairs else 0
        sup = {"status": "degraded", "reason": str(exc), "accuracy": round(acc, 4), "n": len(pairs)}
    return {
        "run": name,
        "n_scored": len(keys),
        "silver_negative": len(s_neg),
        "missed_negative": len(missed_neg),
        "content_fp": len(fp),
        "content_fp_rate": round(len(fp) / len(no_c), 3) if no_c else 0,
        "content_recall": round(len(tp) / len(has_c), 3) if has_c else None,
        "content_silver_n": len(has_c),
        "primary_supervised": sup,
        "fp_ids": sorted(fp),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="商品內容嚴格界線 A/B 報告")
    ap.add_argument("--eval-set", required=True)
    ap.add_argument("--runs", nargs="+", required=True)
    ap.add_argument("--silver", default="")
    ap.add_argument("--out", default="", help="報告 JSON 輸出路徑（預設只印 stdout）")
    args = ap.parse_args()

    with open(args.eval_set, encoding="utf-8") as f:
        evalset = json.load(f)
    groups = {_key(e["source"], e["source_id"]): e["group"] for e in evalset}
    before_preds = {
        _key(e["source"], e["source_id"]): _pred_of_before(e["before"]) for e in evalset
    }

    all_preds: dict[str, dict[str, dict]] = {"before": before_preds}
    for path in args.runs:
        with open(path, encoding="utf-8") as f:
            run = json.load(f)
        name = run.get("mode") or os.path.basename(path)
        if run.get("stage_a_model"):
            name = f"{name}+A:{run['stage_a_model']}"
        while name in all_preds:  # 同 mode 多檔防覆蓋
            name += "'"
        all_preds[name] = {
            _key(r["source"], r["source_id"]): _pred_of_attrs(r["attrs"]) for r in run["results"]
        }

    report: dict[str, Any] = {"describe": [], "scored": []}
    for name, preds in all_preds.items():
        report["describe"].append(_describe(name, preds, groups))

    if args.silver:
        with open(args.silver, encoding="utf-8") as f:
            silver_list = json.load(f)
        silver = {s["id"]: s for s in silver_list}
        for name, preds in all_preds.items():
            report["scored"].append(_score(name, preds, silver))

    print(json.dumps(report, ensure_ascii=False, indent=1))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
