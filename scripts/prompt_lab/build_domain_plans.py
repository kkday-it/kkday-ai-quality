#!/usr/bin/env python3
"""由域配置生成 C3～C6 Layer 1/2 计划（零 API）。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from domains import load_domain  # noqa: E402
from schemas import Plan, PlanCell  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
PLANS_DIR = ROOT / "evals" / "prompt_lab" / "plans"

POSITIVE_DISTRIBUTION = [
    ("direct", 3, "easy", "negative"),
    ("colloquial", 2, "medium", "negative"),
    ("euphemistic", 2, "medium", "negative"),
    ("rhetorical_question", 1, "hard", "negative"),
    ("noisy", 1, "medium", "negative"),
    ("neutral_mixed", 1, "hard", "neutral"),
]
PAIR_VARIANTS = ["direct", "colloquial", "euphemistic"]
ADVERSARIAL = ["negation", "sarcasm", "mixed_language", "prompt_injection"]


def _slug(domain: str) -> str:
    return domain.lower().replace("-", "")


def build_layer1(cfg: dict) -> Plan:
    domain, slug = cfg["domain"], _slug(cfg["domain"])
    cells: list[PlanCell] = []
    for item in cfg["l2"]:
        code = item["code"]
        for idx, (variant, count, difficulty, polarity) in enumerate(POSITIVE_DISTRIBUTION, 1):
            cells.append(PlanCell(
                cell_id=f"{slug}-l1-pos-{code.lower().replace('-', '')}-{idx:02d}",
                domain_under_test=domain, layer=1, expected_domain="true",
                focus_l2=code, target_l2_codes=[code], expression_variant=variant,
                difficulty=difficulty, input_polarity=polarity,
                case_family="rule_unit", target_count=count,
                coverage_note=item["positive_contract"],
            ))
    first_l2 = cfg["l2"][0]["code"]
    for neg_domain in cfg["negative_domains"]:
        for group in range(1, 3):
            cells.append(PlanCell(
                cell_id=f"{slug}-l1-neg-{neg_domain.lower().replace('-', '')}-{group}",
                domain_under_test=domain, layer=1, expected_domain="false",
                focus_l2=first_l2, boundary_with=neg_domain,
                expression_variant="direct" if group == 1 else "colloquial",
                difficulty="medium", input_polarity="negative",
                case_family="rule_unit", target_count=5,
                coverage_note=cfg["negative_contracts"][neg_domain],
            ))
    for group in range(1, 3):
        cells.append(PlanCell(
            cell_id=f"{slug}-l1-defensive-{group}", domain_under_test=domain,
            layer=1, expected_domain="false", focus_l2=first_l2,
            boundary_with="no_issue", expression_variant="positive",
            difficulty="easy", input_polarity="positive",
            case_family="defensive_positive", target_count=5,
            coverage_note="自然的满意或礼貌评论，不含任何具体问题点。",
        ))
    target = sum(c.target_count for c in cells)
    if target != cfg["generation"]["layer1_target"]:
        raise ValueError(f"{domain} Layer1={target}，配置目标={cfg['generation']['layer1_target']}")
    return Plan(plan_id=f"{slug}-layer1-v1", domain_under_test=domain, layer=1,
                description=f"{domain} {cfg['name']} Layer 1", total_target=target, cells=cells)


def build_layer2(cfg: dict) -> Plan:
    domain, slug = cfg["domain"], _slug(cfg["domain"])
    cells: list[PlanCell] = []
    l2_by_code = {x["code"]: x for x in cfg["l2"]}
    for code, boundaries in cfg["domain_boundaries"].items():
        for boundary in boundaries:
            for pair_no, variant in enumerate(PAIR_VARIANTS, 1):
                cells.append(PlanCell(
                    cell_id=f"{slug}-l2-domainpair-{code.lower().replace('-', '')}-{boundary.lower().replace('-', '')}-{pair_no}",
                    domain_under_test=domain, layer=2, expected_domain="pair",
                    focus_l2=code, target_l2_codes=[code], boundary_with=boundary,
                    expression_variant=variant, difficulty="hard", input_polarity="negative",
                    case_family="domain_pair", target_count=2,
                    coverage_note=l2_by_code[code]["positive_contract"],
                    contrast_theme=f"只改变责任站点：{domain} 的决定性事实 vs {boundary} 的决定性事实",
                    pair_group=pair_no,
                ))
        cells.extend([
            PlanCell(
                cell_id=f"{slug}-l2-mixed-{code.lower().replace('-', '')}",
                domain_under_test=domain, layer=2, expected_domain="true",
                focus_l2=code, target_l2_codes=[code], expression_variant="neutral_mixed",
                difficulty="hard", input_polarity="neutral", case_family="mixed",
                target_count=4, coverage_note=l2_by_code[code]["positive_contract"],
            ),
            PlanCell(
                cell_id=f"{slug}-l2-uncertain-{code.lower().replace('-', '')}",
                domain_under_test=domain, layer=2, expected_domain="uncertain",
                focus_l2=code, expression_variant="underspecified", difficulty="hard",
                input_polarity="negative", case_family="uncertain", target_count=4,
                coverage_note=f"围绕 {code} 与近邻责任，但故意缺少决定责任站点的关键事实。",
            ),
            PlanCell(
                cell_id=f"{slug}-l2-adversarial-{code.lower().replace('-', '')}",
                domain_under_test=domain, layer=2, expected_domain="true",
                focus_l2=code, target_l2_codes=[code], expression_variant="adversarial",
                difficulty="hard", input_polarity="negative", case_family="adversarial",
                target_count=4, coverage_note=l2_by_code[code]["positive_contract"],
                adversarial_techniques=ADVERSARIAL,
            ),
        ])
    base_target = sum(c.target_count for c in cells)
    if base_target != cfg["generation"]["layer2_base_target"]:
        raise ValueError(f"{domain} Layer2 base={base_target}，配置目标={cfg['generation']['layer2_base_target']}")
    for a, b in cfg["l2_confusion_pairs"]:
        for pair_no, variant in enumerate(PAIR_VARIANTS, 1):
            cells.append(PlanCell(
                cell_id=f"{slug}-l2-l2pair-{a.lower().replace('-', '')}-{b.lower().replace('-', '')}-{pair_no}",
                domain_under_test=domain, layer=2, expected_domain="pair",
                focus_l2=a, target_l2_codes=[a, b], boundary_with=b,
                expression_variant=variant, difficulty="hard", input_polarity="negative",
                case_family="l2_pair", target_count=2,
                coverage_note=f"A={a} {l2_by_code[a]['positive_contract']}；B={b} {l2_by_code[b]['positive_contract']}",
                contrast_theme=f"只改变决定 L2 的事实：{a} vs {b}；两侧都属于 {domain}",
                pair_group=pair_no,
            ))
    pair_target = sum(c.target_count for c in cells) - base_target
    if pair_target != cfg["generation"]["l2_pair_target"]:
        raise ValueError(f"{domain} L2 pair={pair_target}，配置目标={cfg['generation']['l2_pair_target']}")
    target = sum(c.target_count for c in cells)
    return Plan(plan_id=f"{slug}-layer2-v1", domain_under_test=domain, layer=2,
                description=f"{domain} {cfg['name']} Layer 2（含 domain_pair/l2_pair）",
                total_target=target, cells=cells)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="由域配置生成 Prompt Lab 计划")
    ap.add_argument("--domain", required=True, choices=["C-3", "C-4", "C-5", "C-6"])
    args = ap.parse_args(argv)
    cfg = load_domain(args.domain)
    plans = [build_layer1(cfg), build_layer2(cfg)]
    PLANS_DIR.mkdir(parents=True, exist_ok=True)
    for plan in plans:
        out = PLANS_DIR / f"{_slug(args.domain)}_layer{plan.layer}_plan.json"
        out.write_text(json.dumps(plan.model_dump(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"✅ {out}: {len(plan.cells)} 格 → {plan.total_target} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
