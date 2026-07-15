#!/usr/bin/env python3
"""汇总四域 audited-candidate baseline 指标、错误簇与 Excel 输入 JSON。"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
DOMAIN_INFO = {
    "C-3": ("c3", "供应商履约", "03_C-3_supplier.md"),
    "C-4": ("c4", "平台与系统", "04_C-4_platform.md"),
    "C-5": ("c5", "客服营运", "05_C-5_service.md"),
    "C-6": ("c6", "理解期待", "06_C-6_customer.md"),
}


def safe(n: float, d: float) -> float | None:
    return round(n / d, 4) if d else None


def error_cluster(c: dict, r: dict | None) -> str:
    if not r or r.get("error"):
        return "api_or_schema"
    hit = bool(r.get("predicted_domain_hit"))
    if c["expected_domain"] == "uncertain":
        return "uncertain_forced" if hit else "correct"
    gold = c["expected_domain"] == "true"
    if hit != gold:
        return "domain_false_negative" if gold else "domain_false_positive"
    if gold and set(r.get("predicted_l2_codes") or []) != set(c.get("expected_l2_codes") or []):
        return f"l2:{'+'.join(c.get('expected_l2_codes') or [])}→{'+'.join(r.get('predicted_l2_codes') or []) or 'empty'}"
    if hit and r.get("evidence_grounded") is False:
        return "evidence_not_grounded"
    return "correct"


def load_domain(domain: str, tmp_root: Path) -> dict:
    slug, name, judge_file = DOMAIN_INFO[domain]
    gen_dir = tmp_root / f"{slug}-gemini35-5rounds"
    run_dir = gen_dir / "judge-run-gpt54mini-high"
    candidates = common.read_jsonl(gen_dir / f"{slug}-all-candidates.jsonl")
    audits = common.read_jsonl(gen_dir / f"{slug}-all-audits.jsonl")
    results = common.read_jsonl(run_dir / "raw_results.jsonl")
    metric_payload = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
    run_manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    gen_manifest = json.loads((gen_dir / "generation-manifest.json").read_text(encoding="utf-8"))
    au = {x["case_id"]: x for x in audits}
    rr = {x["case_id"]: x for x in results}
    disagreements = 0
    for c in candidates:
        a = au.get(c["case_id"])
        if not a:
            disagreements += 1
        elif a["suggested_domain"] != c["expected_domain"]:
            disagreements += 1
        elif c["expected_domain"] == "true" and set(a.get("suggested_l2_codes") or []) != set(c.get("expected_l2_codes") or []):
            disagreements += 1
    clusters: Counter[str] = Counter()
    reps: dict[str, str] = {}
    for c in candidates:
        cluster = error_cluster(c, rr.get(c["case_id"]))
        if cluster != "correct":
            clusters[cluster] += 1
            reps.setdefault(cluster, c["case_id"])
    m = metric_payload["metrics"]
    l2_items = [(code, row.get("recall")) for code, row in m.get("l2", {}).get("per_l2", {}).items() if row.get("recall") is not None]
    worst_l2 = min(l2_items, key=lambda x: x[1]) if l2_items else ("n/a", None)
    boundary_items = []
    for boundary, row in m.get("slices", {}).get("boundary_with", {}).items():
        if boundary and row.get("n"):
            boundary_items.append((boundary, safe(row.get("fp", 0) + row.get("fn", 0), row["n"]), row))
    worst_boundary = max(boundary_items, key=lambda x: x[1] or 0) if boundary_items else ("n/a", None, {})
    return {
        "domain": domain, "slug": slug, "name": name,
        "candidates": candidates, "audits": audits, "results": results,
        "metrics": m, "gates": metric_payload["gates"], "run_manifest": run_manifest,
        "generation_manifest": gen_manifest,
        "auditor_status": Counter(x.get("status", "missing") for x in audits),
        "auditor_disagreement_rate": safe(disagreements, len(candidates)),
        "clusters": clusters, "cluster_reps": reps,
        "worst_l2": worst_l2, "worst_boundary": worst_boundary,
        "judge_path": ROOT / "evals/prompt_lab/prompts/judges" / judge_file,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tmp-root", default=str(ROOT / "tmp/prompt_lab"))
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args(argv)
    tmp_root, out_dir = Path(args.tmp_root), Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    data = [load_domain(d, tmp_root) for d in DOMAIN_INFO]
    baseline = json.loads((tmp_root / "c3-c6-baseline-manifest.json").read_text(encoding="utf-8"))
    hashes = {}
    for d in data:
        before = baseline["judge_prompts"][d["domain"]]["sha256"]
        after = common.sha256_file(d["judge_path"])
        hashes[d["domain"]] = {"before": before, "after": after, "match": before == after}

    tp = sum(x["metrics"]["domain"]["tp"] for x in data)
    fp = sum(x["metrics"]["domain"]["fp"] for x in data)
    tn = sum(x["metrics"]["domain"]["tn"] for x in data)
    fn = sum(x["metrics"]["domain"]["fn"] for x in data)
    p, r = safe(tp, tp + fp), safe(tp, tp + fn)
    overall = {
        "n_candidates": sum(len(x["candidates"]) for x in data),
        "n_audits": sum(len(x["audits"]) for x in data),
        "n_judge_runs": sum(len(x["results"]) for x in data),
        "domain": {"tp": tp, "fp": fp, "tn": tn, "fn": fn, "precision": p, "recall": r,
                   "specificity": safe(tn, tn + fp), "f1": safe(2 * (p or 0) * (r or 0), (p or 0) + (r or 0))},
    }
    machine = {
        "label_status": "AI synthetic candidate labels plus independent AI audit; not human Gold",
        "models": {"generator": "gemini-3.5-flash", "auditor": data[0]["audits"][0].get("auditor_model", ""),
                   "judge": data[0]["run_manifest"]["model"]},
        "judge_config": data[0]["run_manifest"]["request_config"],
        "judge_prompt_hashes": hashes, "overall": overall,
        "domains": {
            x["domain"]: {
                "name": x["name"], "n_candidates": len(x["candidates"]), "n_audits": len(x["audits"]),
                "n_judge_runs": len(x["results"]), "generation": x["generation_manifest"],
                "auditor_status": dict(x["auditor_status"]), "auditor_disagreement_rate": x["auditor_disagreement_rate"],
                "metrics": x["metrics"], "gates": x["gates"],
                "worst_l2": {"code": x["worst_l2"][0], "recall": x["worst_l2"][1]},
                "worst_boundary": {"boundary": x["worst_boundary"][0], "error_rate": x["worst_boundary"][1]},
                "error_clusters": [{"cluster": k, "count": v, "representative_case_id": x["cluster_reps"][k]} for k, v in x["clusters"].most_common()],
            } for x in data
        },
    }
    (out_dir / "metrics.json").write_text(json.dumps(machine, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# C3～C6 Gemini 3.5 / GPT-5.4-mini High Baseline 汇总", "",
        "> **重要：这是 AI 合成候选标签＋独立 AI Auditor，不是人工 Gold；以下 CI 只反映候选集统计波动，不代表线上真实准确率。**", "",
        "## 完成情况", "",
        f"- 候选／Auditor／Judge：{overall['n_candidates']} / {overall['n_audits']} / {overall['n_judge_runs']}",
        f"- Generator：`gemini-3.5-flash`；Auditor：`{machine['models']['auditor']}`；Judge：`{machine['models']['judge']}`。",
        f"- Judge 配置：temperature=1，reasoning_effort=high，thinking=true，repeats=1，cache=disabled。", "",
        "## Judge Prompt SHA-256", "",
    ]
    for domain, h in hashes.items():
        lines.append(f"- {domain}: `{h['before']}` → `{h['after']}`｜{'一致' if h['match'] else '不一致'}")
    lines += ["", "## 每域核心指标", "", "| 域 | 样本 | P | R | Specificity | F1 | L2 Exact | Domain Pair | L2 Pair | 95% CI (F1) |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for x in data:
        m = x["metrics"]
        lines.append(f"| {x['domain']} {x['name']} | {len(x['candidates'])} | {m['domain']['precision']} | {m['domain']['recall']} | {m['domain']['specificity']} | {m['domain']['f1']} | {m['l2'].get('exact_set_accuracy')} | {m['domain_pair'].get('pair_both_correct_rate')} | {m['l2_pair'].get('pair_both_correct_rate')} | {m['bootstrap_95ci'].get('f1')} |")
    lines += ["", "## 最差切片与错误簇", ""]
    for x in data:
        top = ", ".join(f"{k}={v}（{x['cluster_reps'][k]}）" for k, v in x["clusters"].most_common(5)) or "无"
        lines.append(f"- {x['domain']}：最差 L2 `{x['worst_l2'][0]}` recall={x['worst_l2'][1]}；最差 boundary `{x['worst_boundary'][0]}` error_rate={x['worst_boundary'][1]}；Top 错误簇：{top}。")
    lines += ["", "## API 失败与数据缺口", ""]
    for x in data:
        lines.append(f"- {x['domain']}：Generator 去重丢弃 {x['generation_manifest']['duplicates_dropped']}；Auditor 缺失 {len(x['candidates'])-len(x['audits'])}；Judge 失败 {x['run_manifest']['n_errors']}。")
    lines += ["", "## 人工审核最短路径", "", "1. 优先审核全部 domain pair、l2 pair、uncertain、Auditor review_required，以及 C3-5/C3-7。", "2. 对其余 accepted 候选分层抽查至少 20%。", "3. 只有人类 accept/edit/reject 后才冻结为 Gold；随后再根据真实错误簇设计 Judge V2，避免把 Generator、Auditor、Judge 的共同偏差固化进 Prompt。", ""]
    (out_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")

    domain_headers = ["域", "名称", "候选", "Auditor", "Judge", "Schema valid", "Precision", "Recall", "Specificity", "F1", "L2 Exact", "Domain Pair", "L2 Pair", "Auditor 分歧率", "Judge 失败"]
    domain_rows, l2_rows, boundary_rows, error_rows = [], [], [], []
    for x in data:
        m = x["metrics"]
        domain_rows.append([x["domain"], x["name"], len(x["candidates"]), len(x["audits"]), len(x["results"]), m["schema_valid_rate"], m["domain"]["precision"], m["domain"]["recall"], m["domain"]["specificity"], m["domain"]["f1"], m["l2"].get("exact_set_accuracy"), m["domain_pair"].get("pair_both_correct_rate"), m["l2_pair"].get("pair_both_correct_rate"), x["auditor_disagreement_rate"], x["run_manifest"]["n_errors"]])
        for code, row in m["l2"].get("per_l2", {}).items():
            l2_rows.append([x["domain"], code, row.get("tp"), row.get("fp"), row.get("fn"), row.get("precision"), row.get("recall"), row.get("f1")])
        for boundary, row in m.get("slices", {}).get("boundary_with", {}).items():
            if boundary:
                boundary_rows.append([x["domain"], boundary, row.get("n"), row.get("fp"), row.get("fn"), row.get("fpr"), row.get("fnr"), safe(row.get("fp", 0)+row.get("fn", 0), row.get("n", 0))])
        for cluster, count in x["clusters"].most_common():
            error_rows.append([x["domain"], cluster, count, x["cluster_reps"][cluster]])
    excel_spec = {
        "domain_headers": domain_headers, "domain_rows": domain_rows,
        "l2_headers": ["域", "L2", "TP", "FP", "FN", "Precision", "Recall", "F1"], "l2_rows": l2_rows,
        "boundary_headers": ["域", "Boundary", "N", "FP", "FN", "FPR", "FNR", "Error Rate"], "boundary_rows": boundary_rows,
        "error_headers": ["域", "错误簇", "数量", "代表 case_id"], "error_rows": error_rows,
    }
    (out_dir / "summary-workbook.json").write_text(json.dumps(excel_spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"✅ summary → {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
