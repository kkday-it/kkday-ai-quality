"""C-2 生成計畫建構器——輸出可供 generate_cases.py 批次消化的兩層 plan。

Layer 1：明确正例 50 + 他域负例 50 + 防御正向 10 = 110。
Layer 2：最小对照 90 + 混合 20 + 不确定 20 + 对抗 20 = 150。
本脚本纯离线、零 API；执行：python scripts/prompt_lab/build_c2_plans.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from schemas import C2_L2_CODES, Plan, PlanCell  # noqa: E402

L2_COVERAGE: dict[str, str] = {
    "C-2-1": "已成功启用并开始使用后的讯号微弱、网速慢、频繁断线或完全无网路",
    "C-2-2": "餐点温度、味道、食材新鲜度、选择数量或用餐区卫生存在具体瑕疵",
    "C-2-3": "车辆本身老旧、异音、故障、冷气或座椅等一般乘坐设备状态不佳；避免安全配备题",
    "C-2-4": "住宿房内脏污、异味、虫害、冷气热水故障、隔音或备品清洁问题",
    "C-2-5": "活动器材、游具、展馆或场地设备老旧、故障、不洁或维护不良；避免人身安全风险题",
}

POS_VARIANT_DIST: list[tuple[str, int, str, str]] = [
    ("direct", 3, "easy", "negative"),
    ("colloquial", 2, "medium", "negative"),
    ("euphemistic", 2, "medium", "negative"),
    ("rhetorical_question", 1, "hard", "negative"),
    ("noisy", 1, "medium", "negative"),
    ("neutral_mixed", 1, "hard", "neutral"),
]
NEG_VARIANT_DIST: list[tuple[str, int, str]] = [
    ("direct", 3, "medium"),
    ("colloquial", 3, "medium"),
    ("euphemistic", 2, "hard"),
    ("rhetorical_question", 2, "hard"),
]
DEF_VARIANT_DIST: list[tuple[str, int, str]] = [
    ("direct", 3, "easy"),
    ("colloquial", 3, "easy"),
    ("euphemistic", 2, "medium"),
    ("noisy", 2, "medium"),
]

# 负例每个邻域 10 条；focus_l2 只用于切片，不代表负例的 expected L2。
NEG_DOMAINS: list[tuple[str, str, str]] = [
    ("C-1", "C-2-3", "页面资讯、等级或交付内容描述错误；交付物本身没有客观品质瑕疵"),
    ("C-3", "C-2-5", "现场人员态度、驾驶、调度、未提供、操作失当或安全/公共卫生管理问题"),
    ("C-4", "C-2-1", "尚未成功开通、启用、核销或通过资格验证，不能当成连接品质"),
    ("C-5", "C-2-4", "当前投诉焦点是售后退款、回应、改期或申诉处理，品质问题已排除或解决"),
    ("C-6", "C-2-2", "交付物正常，只有个人口味、不值、无聊、外力或旅客自身因素"),
]

# 每个 L2 取三条最容易误归的近邻边界。降规/货不对板与设施安全风险是政策冲突，
# 不放入明确正例；以 C-1/C-3 对照观察并要求人工复核。
BOUNDARY_MATRIX: dict[str, list[str]] = {
    "C-2-1": ["C-4-1", "C-1", "no_issue"],
    "C-2-2": ["C-6-3", "C-3-5", "C-1"],
    "C-2-3": ["C-3-2", "C-3-5", "C-1"],
    "C-2-4": ["C-1", "C-3-4", "C-3-5"],
    "C-2-5": ["C-1", "C-3", "C-6"],
}
PAIRS_PER_BOUNDARY = 3
PAIR_VARIANTS = ["direct", "colloquial", "euphemistic"]
ADVERSARIAL_TECHNIQUES = [
    "negation_reversal",
    "complain_then_clarify",
    "rhetorical",
    "sarcasm",
    "simplified_traditional_mix",
    "multilingual",
    "emoji",
    "typo",
    "length_extreme",
    "prompt_injection",
]


def build_layer1() -> Plan:
    cells: list[PlanCell] = []
    for l2 in C2_L2_CODES:
        for variant, count, difficulty, polarity in POS_VARIANT_DIST:
            cells.append(
                PlanCell(
                    cell_id=f"c2-l1-pos-{l2}-{variant}",
                    domain_under_test="C-2",
                    layer=1,
                    expected_domain="true",
                    focus_l2=l2,
                    target_l2_codes=[l2],
                    expression_variant=variant,
                    difficulty=difficulty,
                    input_polarity=polarity,
                    case_family="rule_unit",
                    target_count=count,
                    coverage_note=f"{l2} 正例：{L2_COVERAGE[l2]}",
                )
            )
    for domain, focus_l2, note in NEG_DOMAINS:
        for variant, count, difficulty in NEG_VARIANT_DIST:
            cells.append(
                PlanCell(
                    cell_id=f"c2-l1-neg-{domain}-{variant}",
                    domain_under_test="C-2",
                    layer=1,
                    expected_domain="false",
                    focus_l2=focus_l2,
                    boundary_with=domain,
                    expression_variant=variant,
                    difficulty=difficulty,
                    input_polarity="negative",
                    case_family="negative_other_domain",
                    target_count=count,
                    coverage_note=f"真实责任={domain}：{note}；不得含独立成立的 C-2 客观品质问题",
                )
            )
    for i, (variant, count, difficulty) in enumerate(DEF_VARIANT_DIST):
        cells.append(
            PlanCell(
                cell_id=f"c2-l1-def-{variant}",
                domain_under_test="C-2",
                layer=1,
                expected_domain="false",
                focus_l2=C2_L2_CODES[i % len(C2_L2_CODES)],
                expression_variant=variant,
                difficulty=difficulty,
                input_polarity="positive",
                case_family="defensive_positive",
                target_count=count,
                coverage_note="纯正向且没有任何品质问题；期望空归因，仅用于防御性统计",
            )
        )
    return Plan(
        plan_id="c2-layer1",
        domain_under_test="C-2",
        layer=1,
        description="C-2 Layer 1：明确正例 50 + 他域负例 50 + 防御正向 10 = 110",
        total_target=110,
        cells=cells,
    )


def build_layer2() -> Plan:
    cells: list[PlanCell] = []
    for l2 in C2_L2_CODES:
        for boundary in BOUNDARY_MATRIX[l2]:
            for group in range(1, PAIRS_PER_BOUNDARY + 1):
                cells.append(
                    PlanCell(
                        cell_id=f"c2-l2-pair-{l2}-{boundary}-{group}",
                        domain_under_test="C-2",
                        layer=2,
                        expected_domain="pair",
                        focus_l2=l2,
                        target_l2_codes=[l2],
                        boundary_with=boundary,
                        expression_variant=PAIR_VARIANTS[group - 1],
                        difficulty="hard",
                        input_polarity="negative",
                        case_family="contrast_pair",
                        target_count=2,
                        pair_group=group,
                        contrast_theme=(
                            f"交付物是否存在【{L2_COVERAGE[l2]}】的可观察品质瑕疵——"
                            f"A 侧命中 {l2}；B 侧交付物本身正常、真实责任在 {boundary}；仅改变这一项责任事实"
                        ),
                        coverage_note=f"{l2} vs {boundary} 最小对照（pair {group}）",
                    )
                )
    for l2 in C2_L2_CODES:
        cells.append(
            PlanCell(
                cell_id=f"c2-l2-mixed-{l2}",
                domain_under_test="C-2",
                layer=2,
                expected_domain="true",
                focus_l2=l2,
                target_l2_codes=[l2],
                expression_variant="neutral_mixed",
                difficulty="hard",
                input_polarity="neutral",
                case_family="mixed",
                target_count=4,
                coverage_note=f"{l2} 混合：整体满意，但包含一个可逐字引用的客观品质瑕疵",
            )
        )
        cells.append(
            PlanCell(
                cell_id=f"c2-l2-unc-{l2}",
                domain_under_test="C-2",
                layer=2,
                expected_domain="uncertain",
                focus_l2=l2,
                expression_variant="ambiguous",
                difficulty="hard",
                input_polarity="negative",
                case_family="uncertain",
                target_count=4,
                coverage_note=f"{l2} 相关但只写很差/不能用等结论，无法区分品质、系统、执行或主观期待",
            )
        )
    for i, l2 in enumerate(C2_L2_CODES):
        techniques = [
            ADVERSARIAL_TECHNIQUES[(i * 4 + k) % len(ADVERSARIAL_TECHNIQUES)]
            for k in range(4)
        ]
        cells.append(
            PlanCell(
                cell_id=f"c2-l2-adv-{l2}",
                domain_under_test="C-2",
                layer=2,
                expected_domain="true",
                focus_l2=l2,
                target_l2_codes=[l2],
                expression_variant="adversarial",
                difficulty="hard",
                input_polarity="negative",
                case_family="adversarial",
                target_count=4,
                adversarial_techniques=techniques,
                coverage_note=f"{l2} 客观品质事实以 {', '.join(techniques)} 包装，标签仍成立",
            )
        )
    return Plan(
        plan_id="c2-layer2",
        domain_under_test="C-2",
        layer=2,
        description="C-2 Layer 2：最小对照 90 + 混合 20 + 不确定 20 + 对抗 20 = 150",
        total_target=150,
        cells=cells,
    )


def main() -> None:
    out_dir = Path(__file__).resolve().parents[2] / "evals" / "prompt_lab" / "plans"
    out_dir.mkdir(parents=True, exist_ok=True)
    for plan, filename in (
        (build_layer1(), "c2_layer1_plan.json"),
        (build_layer2(), "c2_layer2_plan.json"),
    ):
        path = out_dir / filename
        path.write_text(
            json.dumps(plan.model_dump(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            f"✅ {filename}: {len(plan.cells)} 格, total_target={plan.total_target}, "
            f"sum={sum(c.target_count for c in plan.cells)} → {path}"
        )


if __name__ == "__main__":
    main()
