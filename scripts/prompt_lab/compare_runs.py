"""Baseline vs Candidate Prompt 回歸對比（PRD §12 晉級條件 / §13 diff 報告）。

在**同一資料集**上比較兩次 run：fixed / regressed / unchanged_wrong、confidence shift、
slice delta、cost/latency 變化。任一新增錯誤都進 diff（可追溯）。純函式，零 API。

晉級條件（§12，建議性判定）：目標邊界改善；Layer 1 核心不得下降 >1pp；非目標邊界無顯著 FP 增長；
Holdout 達標；所有新增錯誤入 diff。
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
import metrics as M  # noqa: E402

LAYER1_DROP_TOLERANCE = 0.01  # §12：Layer 1 核心指標不得下降超過 1 個百分點


def _load_run(run_dir: str) -> tuple[list[dict], list[dict], dict]:
    """讀一個 run 目錄：raw_results + 其 dataset（由 manifest 指路）+ manifest。"""
    d = Path(run_dir)
    results = common.read_jsonl(d / "raw_results.jsonl")
    man = (
        json.loads((d / "run_manifest.json").read_text(encoding="utf-8"))
        if (d / "run_manifest.json").exists()
        else {}
    )
    cases = common.read_jsonl(man.get("dataset", "")) if man.get("dataset") else []
    return cases, results, man


def _case_correct(cases: list[dict], results: list[dict]) -> dict[str, bool]:
    """每 case 域是否正確（多數 repeat 為準）：true/false 比對 gold；uncertain 以「多數棄權」為正確。"""
    by = {c["case_id"]: c for c in cases}
    grp: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        grp[r["case_id"]].append(r)
    out: dict[str, bool] = {}
    for cid, rs in grp.items():
        c = by.get(cid)
        if not c:
            continue
        hits = [
            bool(x.get("predicted_domain_hit"))
            for x in rs
            if x.get("predicted_domain_hit") is not None
        ]
        if not hits:
            out[cid] = False
            continue
        maj_hit = sum(hits) >= (len(hits) + 1) // 2
        if c["expected_domain"] == "uncertain":
            out[cid] = not maj_hit  # 棄權為正確
        else:
            out[cid] = maj_hit == (c["expected_domain"] == "true")
    return out


def _cost(man: dict, results: list[dict]) -> dict:
    return {
        "input_tokens": man.get("total_input_tokens")
        or sum(r.get("input_tokens") or 0 for r in results),
        "output_tokens": man.get("total_output_tokens")
        or sum(r.get("output_tokens") or 0 for r in results),
        "latency_ms": man.get("total_latency_ms")
        or sum(r.get("latency_ms") or 0 for r in results),
    }


def compare(base_dir: str, cand_dir: str) -> dict:
    """回完整 diff dict。"""
    bc, br, bm = _load_run(base_dir)
    cc, cr, cm = _load_run(cand_dir)
    b_ok, c_ok = _case_correct(bc, br), _case_correct(cc, cr)
    shared = sorted(set(b_ok) & set(c_ok))
    by = {c["case_id"]: c for c in (cc or bc)}

    fixed, regressed, unchanged_wrong = [], [], []
    for cid in shared:
        row = {
            "case_id": cid,
            "expected_domain": by.get(cid, {}).get("expected_domain"),
            "boundary_with": by.get(cid, {}).get("boundary_with") or "",
            "case_family": by.get(cid, {}).get("case_family"),
        }
        if not b_ok[cid] and c_ok[cid]:
            fixed.append(row)
        elif b_ok[cid] and not c_ok[cid]:
            regressed.append(row)
        elif not b_ok[cid] and not c_ok[cid]:
            unchanged_wrong.append(row)

    bfull = M.compute_all(bc, br)
    cfull = M.compute_all(cc, cr)
    # slice delta（域指標 recall/precision/fpr）
    brows, crows = M.build_rows(bc, br), M.build_rows(cc, cr)
    slice_delta = {}
    for dim in ("boundary_with", "case_family", "expected_domain", "layer"):
        bs, cs = (
            M.sliced(brows, dim, M.domain_metrics),
            M.sliced(crows, dim, M.domain_metrics),
        )
        for k in sorted(set(bs) | set(cs)):
            for metric in ("recall", "precision", "fpr", "specificity"):
                bv, cv = (bs.get(k) or {}).get(metric), (cs.get(k) or {}).get(metric)
                if bv is not None and cv is not None and abs(cv - bv) >= 1e-9:
                    slice_delta[f"{dim}={k}:{metric}"] = {
                        "baseline": bv,
                        "candidate": cv,
                        "delta": round(cv - bv, 4),
                    }

    # confidence shift
    def _mean_conf(full):
        s = full["stability"]
        rng = s.get("confidence_range")
        return round(sum(rng) / 2, 4) if rng else None

    bcost, ccost = _cost(bm, br), _cost(cm, cr)
    # 晉級判定（建議性）
    l1_drop = _layer1_regressions(bfull, cfull, bc, br, cc, cr)
    return {
        "baseline": base_dir,
        "candidate": cand_dir,
        "n_shared_cases": len(shared),
        "fixed": fixed,
        "regressed": regressed,
        "unchanged_wrong": unchanged_wrong,
        "confidence_shift": {
            "baseline": _mean_conf(bfull),
            "candidate": _mean_conf(cfull),
        },
        "cost": {
            "baseline": bcost,
            "candidate": ccost,
            "delta": {k: ccost[k] - bcost[k] for k in bcost},
        },
        "slice_delta": slice_delta,
        "promotion": {
            "n_fixed": len(fixed),
            "n_regressed": len(regressed),
            "layer1_core_regressions_over_1pp": l1_drop,
            "advisory": "晉級需：目標邊界改善且 Layer1 核心不降 >1pp 且 Holdout 達標（見各自 run 的 gates）。",
        },
    }


def _layer1_regressions(bfull, cfull, bc, br, cc, cr) -> list[dict]:
    """Layer 1 核心指標下降 >1pp 的清單（§12）。"""
    b1 = M.domain_metrics([r for r in M.build_rows(bc, br) if r.layer == 1])
    c1 = M.domain_metrics([r for r in M.build_rows(cc, cr) if r.layer == 1])
    out = []
    for k in ("recall", "specificity", "precision"):
        bv, cv = b1.get(k), c1.get(k)
        if bv is not None and cv is not None and (bv - cv) > LAYER1_DROP_TOLERANCE:
            out.append(
                {
                    "metric": f"layer1_domain_{k}",
                    "baseline": bv,
                    "candidate": cv,
                    "drop": round(bv - cv, 4),
                }
            )
    return out


def _write_csv(path: Path, rows: list[dict], cols: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    """CLI 入口。"""
    ap = argparse.ArgumentParser(description="baseline vs candidate 對比")
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    diff = compare(args.baseline, args.candidate)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "diff.json").write_text(
        json.dumps(diff, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    cols = ["case_id", "expected_domain", "boundary_with", "case_family"]
    _write_csv(out / "fixed.csv", diff["fixed"], cols)
    _write_csv(out / "regressed.csv", diff["regressed"], cols)
    _write_csv(out / "unchanged_wrong.csv", diff["unchanged_wrong"], cols)
    # summary.md
    lines = [
        "# Prompt Diff：baseline vs candidate\n",
        f"- baseline：`{args.baseline}`\n- candidate：`{args.candidate}`",
        f"- 共同 case：{diff['n_shared_cases']}",
        f"- ✅ fixed：{len(diff['fixed'])}｜❌ regressed：{len(diff['regressed'])}｜⚠️ unchanged_wrong：{len(diff['unchanged_wrong'])}\n",
        f"- confidence：baseline={diff['confidence_shift']['baseline']} → candidate={diff['confidence_shift']['candidate']}",
        f"- cost delta：{diff['cost']['delta']}\n",
        "## Layer 1 核心回退（>1pp）",
    ]
    lines += [
        f"- ❌ {r['metric']}: {r['baseline']}→{r['candidate']} (drop {r['drop']})"
        for r in diff["promotion"]["layer1_core_regressions_over_1pp"]
    ] or ["- ✅ 無 >1pp 回退"]
    lines.append("\n## slice delta（|Δ|>0）")
    lines += [
        f"- {k}: {v['baseline']}→{v['candidate']} (Δ{v['delta']:+.4f})"
        for k, v in sorted(diff["slice_delta"].items())
    ] or ["- （無）"]
    lines.append(f"\n> {diff['promotion']['advisory']}")
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        f"✅ diff：fixed {len(diff['fixed'])} / regressed {len(diff['regressed'])} / unchanged_wrong {len(diff['unchanged_wrong'])} → {out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
