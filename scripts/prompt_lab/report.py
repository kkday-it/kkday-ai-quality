"""報告產出（PRD §12 / §13）——metrics.json、summary.md、errors/unstable/boundary/contrast CSV + 門檻判定。

純函式（零 API）。門檻為 §12 的 Mock 工程目標（非上線門檻，上線前須以真實 Gold 重定）。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import metrics as M

# §12 Mock 工程目標（可調；非最終上線門檻）。op: ">=" 或 "<="。
THRESHOLDS = {
    "engineering": {
        "schema_valid_rate": (">=", 1.0),
        "evidence_grounding_quote_rate": (">=", 1.0),
        "traceability_rate": (">=", 1.0),
    },
    "layer1": {
        "domain_recall": (">=", 0.95),
        "domain_specificity": (">=", 0.95),
        "l2_exact": (">=", 0.90),
        "neutral_recall": (">=", 0.90),
        "domain_full_agreement": (">=", 0.95),
    },
    "layer2": {
        "domain_precision": (">=", 0.90),
        "domain_recall": (">=", 0.90),
        "pair_both_correct": (">=", 0.85),
        "max_boundary_fpr": ("<=", 0.15),
        "uncertain_forced_attribution": ("<=", 0.30),
        "domain_full_agreement": (">=", 0.90),
    },
}


def _cmp(value, op: str, thr: float) -> str:
    """回 'pass'/'fail'/'n/a'（value None → n/a）。"""
    if value is None:
        return "n/a"
    return "pass" if ((value >= thr) if op == ">=" else (value <= thr)) else "fail"


def _layer_rows(rows: list, layer: int) -> list:
    return [r for r in rows if r.layer == layer]


def evaluate_gates(cases: list[dict], results: list[dict], full: dict) -> dict:
    """對照 §12 門檻，回各層 pass/fail 詳表。"""
    rows = M.build_rows(cases, results)
    traceable = sum(
        1
        for r in results
        if r.get("prompt_sha256")
        and r.get("model")
        and (r.get("request_id") or r.get("error"))
    )
    gates: dict = {"engineering": {}, "layer1": {}, "layer2": {}}

    eng = THRESHOLDS["engineering"]
    gates["engineering"]["schema_valid_rate"] = _mk(
        full["schema_valid_rate"], *eng["schema_valid_rate"]
    )
    gates["engineering"]["evidence_grounding_quote_rate"] = _mk(
        full["evidence"].get("grounding_quote_rate"),
        *eng["evidence_grounding_quote_rate"],
    )
    gates["engineering"]["traceability_rate"] = _mk(
        M._safe(traceable, len(results)), *eng["traceability_rate"]
    )

    for layer, key in ((1, "layer1"), (2, "layer2")):
        lr = _layer_rows(rows, layer)
        if not lr:
            continue
        dm = M.domain_metrics(lr)
        st = M.stability_metrics(lr)
        if layer == 1:
            l2 = M.l2_metrics(lr)
            neutral = M.domain_metrics([r for r in lr if r.input_polarity == "neutral"])
            g = THRESHOLDS["layer1"]
            gates["layer1"] = {
                "domain_recall": _mk(dm["recall"], *g["domain_recall"]),
                "domain_specificity": _mk(dm["specificity"], *g["domain_specificity"]),
                "l2_exact": _mk(l2.get("exact_set_accuracy"), *g["l2_exact"]),
                "neutral_recall": _mk(neutral["recall"], *g["neutral_recall"]),
                "domain_full_agreement": _mk(
                    st["domain_full_agreement"], *g["domain_full_agreement"]
                ),
            }
        else:
            ct = M.contrast_metrics(lr)
            unc = M.uncertain_metrics(lr)
            bslice = M.sliced(
                [r for r in lr if r.boundary_with], "boundary_with", M.domain_metrics
            )
            fprs = [v["fpr"] for v in bslice.values() if v.get("fpr") is not None]
            g = THRESHOLDS["layer2"]
            gates["layer2"] = {
                "domain_precision": _mk(dm["precision"], *g["domain_precision"]),
                "domain_recall": _mk(dm["recall"], *g["domain_recall"]),
                "pair_both_correct": _mk(
                    ct.get("pair_both_correct_rate"), *g["pair_both_correct"]
                ),
                "max_boundary_fpr": _mk(
                    max(fprs) if fprs else None, *g["max_boundary_fpr"]
                ),
                "uncertain_forced_attribution": _mk(
                    unc.get("forced_attribution_rate"),
                    *g["uncertain_forced_attribution"],
                ),
                "domain_full_agreement": _mk(
                    st["domain_full_agreement"], *g["domain_full_agreement"]
                ),
            }
    gates["overall_pass"] = all(
        v["verdict"] != "fail"
        for grp in gates.values()
        if isinstance(grp, dict)
        for v in grp.values()
        if isinstance(v, dict)
    )
    return gates


def _mk(value, op: str, thr: float) -> dict:
    return {"value": value, "op": op, "threshold": thr, "verdict": _cmp(value, op, thr)}


def _write_csv(path: Path, cols: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _errors_rows(cases: list[dict], results: list[dict]) -> list[dict]:
    """每條錯誤 run：API/schema 失敗，或預測 != gold（域錯/L2 錯）。"""
    by = {c["case_id"]: c for c in cases}
    out = []
    for r in results:
        c = by.get(r["case_id"])
        if not c:
            continue
        gold_hit = c["expected_domain"] == "true"
        err = r.get("error")
        wrong_domain = (
            (c["expected_domain"] != "uncertain")
            and (r.get("predicted_domain_hit") is not None)
            and (bool(r.get("predicted_domain_hit")) != gold_hit)
        )
        wrong_l2 = (
            gold_hit
            and r.get("schema_valid")
            and set(r.get("predicted_l2_codes") or [])
            != set(c.get("expected_l2_codes") or [])
        )
        if err or wrong_domain or wrong_l2:
            out.append(
                {
                    "case_id": r["case_id"],
                    "repeat": r.get("repeat_index"),
                    "layer": c.get("layer"),
                    "case_family": c.get("case_family"),
                    "boundary_with": c.get("boundary_with") or "",
                    "expected_domain": c["expected_domain"],
                    "pred_hit": r.get("predicted_domain_hit"),
                    "expected_l2": "|".join(c.get("expected_l2_codes") or []),
                    "pred_l2": "|".join(r.get("predicted_l2_codes") or []),
                    "evidence_grounded": r.get("evidence_grounded"),
                    "error": err or ("wrong_domain" if wrong_domain else "wrong_l2"),
                    "text": c.get("text", "")[:120],
                }
            )
    return out


def _unstable_rows(cases: list[dict], results: list[dict]) -> list[dict]:
    """跨 repeat 域或 L2 翻轉的 case。"""
    from collections import defaultdict

    by = {c["case_id"]: c for c in cases}
    grp: dict[str, list] = defaultdict(list)
    for r in results:
        grp[r["case_id"]].append(r)
    out = []
    for cid, rs in grp.items():
        if len(rs) < 2:
            continue
        doms = [bool(x.get("predicted_domain_hit")) for x in rs]
        l2s = [tuple(sorted(x.get("predicted_l2_codes") or [])) for x in rs]
        if len(set(doms)) > 1 or len(set(l2s)) > 1:
            c = by.get(cid, {})
            out.append(
                {
                    "case_id": cid,
                    "layer": c.get("layer"),
                    "case_family": c.get("case_family"),
                    "expected_domain": c.get("expected_domain"),
                    "domain_preds": "|".join(str(d) for d in doms),
                    "l2_preds": " / ".join(",".join(t) for t in l2s),
                }
            )
    return out


def write_reports(
    out_dir: str | Path,
    cases: list[dict],
    results: list[dict],
    manifest: dict | None = None,
) -> dict:
    """產出全部報告檔（§13）；回 full metrics dict。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    full = M.compute_all(cases, results)
    gates = evaluate_gates(cases, results, full)
    (out_dir / "metrics.json").write_text(
        json.dumps({"metrics": full, "gates": gates}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )

    # errors.csv
    _write_csv(
        out_dir / "errors.csv",
        [
            "case_id",
            "repeat",
            "layer",
            "case_family",
            "boundary_with",
            "expected_domain",
            "pred_hit",
            "expected_l2",
            "pred_l2",
            "evidence_grounded",
            "error",
            "text",
        ],
        _errors_rows(cases, results),
    )
    # unstable_cases.csv
    _write_csv(
        out_dir / "unstable_cases.csv",
        [
            "case_id",
            "layer",
            "case_family",
            "expected_domain",
            "domain_preds",
            "l2_preds",
        ],
        _unstable_rows(cases, results),
    )
    # boundary_matrix.csv（各 boundary 的域指標）
    rows = M.build_rows(cases, results)
    bslice = M.sliced(
        [r for r in rows if r.boundary_with], "boundary_with", M.domain_metrics
    )
    _write_csv(
        out_dir / "boundary_matrix.csv",
        ["boundary_with", "n", "fp", "tn", "fpr", "specificity"],
        [
            {
                "boundary_with": k,
                **{kk: v.get(kk) for kk in ("n", "fp", "tn", "fpr", "specificity")},
            }
            for k, v in bslice.items()
        ],
    )
    # contrast_pairs.csv
    from collections import defaultdict

    pairs: dict[str, dict] = defaultdict(lambda: {"true": [], "false": []})
    bycase = {c["case_id"]: c for c in cases}
    for r in results:
        c = bycase.get(r["case_id"])
        if c and c.get("contrast_pair_id"):
            pairs[c["contrast_pair_id"]][
                "true" if c["expected_domain"] == "true" else "false"
            ].append(bool(r.get("predicted_domain_hit")))
    prows = []
    for pid, s in sorted(pairs.items()):
        pos_ok = s["true"] and sum(s["true"]) >= (len(s["true"]) + 1) // 2
        neg_ok = (
            s["false"]
            and sum(1 for x in s["false"] if not x) >= (len(s["false"]) + 1) // 2
        )
        prows.append(
            {
                "contrast_pair_id": pid,
                "positive_ok": bool(pos_ok),
                "negative_ok": bool(neg_ok),
                "both_correct": bool(pos_ok and neg_ok),
            }
        )
    _write_csv(
        out_dir / "contrast_pairs.csv",
        ["contrast_pair_id", "positive_ok", "negative_ok", "both_correct"],
        prows,
    )

    # summary.md
    (out_dir / "summary.md").write_text(
        _summary_md(full, gates, manifest or {}), encoding="utf-8"
    )
    return full


def _fmt(v) -> str:
    return "n/a" if v is None else (f"{v:.4f}" if isinstance(v, float) else str(v))


def _summary_md(full: dict, gates: dict, man: dict) -> str:
    """組 summary.md（含 hash/model/用量/各層/各 L2/邊界/不確定/穩定/Top 錯誤/門檻）。"""
    L = []
    L.append(f"# C-1 Prompt 評測報告：{man.get('run_id', '')}\n")
    L.append("## 追溯")
    L.append(
        f"- Prompt：`{man.get('prompt_version', '')}` sha256=`{man.get('prompt_sha256', '')}`"
    )
    L.append(
        f"- Dataset：`{man.get('dataset', '')}`｜Model：`{man.get('model', '')}`｜repeats={man.get('repeats', '')}"
    )
    L.append(
        f"- Runs：{man.get('n_runs', '?')}（失敗 {man.get('n_errors', '?')}）｜tokens in/out={man.get('total_input_tokens', '?')}/{man.get('total_output_tokens', '?')}｜latency={man.get('total_latency_ms', '?')}ms\n"
    )
    d = full["domain"]
    L.append("## 域二分類（排除 uncertain 與純正向防禦）")
    L.append(
        f"- P={_fmt(d['precision'])} R={_fmt(d['recall'])} Specificity={_fmt(d['specificity'])} F1={_fmt(d['f1'])} FPR={_fmt(d['fpr'])} FNR={_fmt(d['fnr'])}（n={d['n']}）"
    )
    de = full["defensive"]
    L.append(f"- 純正向防禦：n={de['n']} 誤命中率={_fmt(de['false_hit_rate'])}\n")
    l2 = full["l2"]
    L.append("## L2（僅 expected true）")
    if l2.get("n"):
        L.append(
            f"- Exact={_fmt(l2['exact_set_accuracy'])} Any-hit={_fmt(l2['any_hit_accuracy'])} Over={_fmt(l2['over_attribution_rate'])} Under={_fmt(l2['under_attribution_rate'])} Dup={_fmt(l2['duplicate_rate'])}"
        )
        L.append(
            "- per-L2 F1："
            + ", ".join(f"{k}={_fmt(v['f1'])}" for k, v in l2["per_l2"].items())
        )
    L.append("")
    ev = full["evidence"]
    L.append("## 證據")
    L.append(
        f"- Grounding(run)={_fmt(ev['grounding_run_rate'])} Grounding(quote)={_fmt(ev['grounding_quote_rate'])} Empty={_fmt(ev['empty_evidence_rate'])} ExpectedOverlap={_fmt(ev['expected_overlap_rate'])}\n"
    )
    st = full["stability"]
    L.append("## 穩定性")
    L.append(
        f"- DomainFullAgreement={_fmt(st['domain_full_agreement'])} L2FullAgreement={_fmt(st['l2_set_full_agreement'])} Pairwise={_fmt(st['pairwise_agreement'])}｜flip cases={len(st['flip_cases'])}\n"
    )
    unc = full["uncertain"]
    L.append("## 不確定")
    if unc.get("n"):
        L.append(
            f"- n={unc['n']} Abstain={_fmt(unc['abstain_rate'])} ForcedAttribution={_fmt(unc['forced_attribution_rate'])} forced_L2={unc['forced_l2_distribution']}\n"
        )
    ct = full["contrast"]
    L.append("## 對照組")
    if ct.get("n_pairs"):
        L.append(
            f"- pairs={ct['n_pairs']} BothCorrect={_fmt(ct['pair_both_correct_rate'])} PosAcc={_fmt(ct['positive_side_accuracy'])} NegAcc={_fmt(ct['negative_side_accuracy'])}"
        )
        if ct.get("failure_by_contrast_key"):
            L.append(f"- 失敗 contrast_key：{ct['failure_by_contrast_key']}")
    L.append("")
    L.append("## 門檻判定（§12 Mock 工程目標，非上線門檻）")
    L.append(f"- **overall: {'✅ PASS' if gates.get('overall_pass') else '❌ FAIL'}**")
    for grp in ("engineering", "layer1", "layer2"):
        if gates.get(grp):
            L.append(f"### {grp}")
            for k, v in gates[grp].items():
                mark = {"pass": "✅", "fail": "❌", "n/a": "➖"}[v["verdict"]]
                L.append(f"- {mark} {k}: {_fmt(v['value'])} {v['op']} {v['threshold']}")
    L.append(
        "\n> Mock 分數非真實線上準確率；上線前須用真實 Gold 重新定阈值（PRD §12）。"
    )
    return "\n".join(L) + "\n"
