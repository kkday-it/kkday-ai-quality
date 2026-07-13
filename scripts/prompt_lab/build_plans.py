"""生成計畫建構器（PRD §5.1 / §5.3）——把覆蓋表與邊界矩陣編碼為可讀 plan JSON。

輸出 evals/prompt_lab/plans/c1_layer1_plan.json（130）與 c1_layer2_plan.json（210）。
DoD：各格 target_count 之和嚴格等於 130／210（由 schemas.Plan 驗證）。

此為純建構器（零 API），可重跑；plan JSON 入 Git 供人工檢視與 Generator 消化。
執行：python scripts/prompt_lab/build_plans.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parent)
)  # 允許直接執行時 sibling import
from schemas import C1_L2_CODES, Plan, PlanCell  # noqa: E402

# ── §5.1：每個 C-1 L2 的覆蓋語義（正例生成指引）──────────────────────────────
L2_COVERAGE: dict[str, str] = {
    "C-1-1": "名稱、摘要、特色、圖片或所在地誇大不實或誤導",
    "C-1-2": "時長、步驟、景點清單、交通流程寫錯或缺漏",
    "C-1-3": "門票、必付費用、兒童價格等費用未揭露或寫不清",
    "C-1-4": "集合時間、地點、地圖、方式模糊或前後矛盾",
    "C-1-5": "使用、兌換、憑證、證件要求頁面未說明",
    "C-1-6": "年齡、健康、體能、天候、成團條件未揭露或模糊",
    "C-1-7": "退改政策、出單 SLA、未履約補救頁面未說明",
}

# §5.1：正例每個 L2 的 10 條變體分佈（3 直接 + 2 口語 + 2 委婉 + 1 反問 + 1 噪聲 + 1 neutral 混合）
POS_VARIANT_DIST: list[tuple[str, int, str, str]] = [
    # (variant, count, difficulty, polarity)
    ("direct", 3, "easy", "negative"),
    ("colloquial", 2, "medium", "negative"),
    ("euphemistic", 2, "medium", "negative"),
    ("rhetorical_question", 1, "hard", "negative"),
    ("noisy", 1, "medium", "negative"),
    ("neutral_mixed", 1, "hard", "neutral"),
]

# §5.1：負例 5 個近鄰域各 10 條的變體分佈（3+3+2+2）
NEG_VARIANT_DIST: list[tuple[str, int, str]] = [
    ("direct", 3, "medium"),
    ("colloquial", 3, "medium"),
    ("euphemistic", 2, "hard"),
    ("rhetorical_question", 2, "hard"),
]
# 負例近鄰域 → 常被誤判成的 C-1 面向（僅供切片分組；負例無 L2）
NEG_DOMAINS: list[tuple[str, str, str]] = [
    ("C-2", "C-1-1", "交付物本身客觀品質差（吃/住/坐/連的東西不行），非頁面描述問題"),
    ("C-3", "C-1-2", "現場的人與執行差（偏離表定/等人/提早收團），頁面已寫清"),
    ("C-4", "C-1-5", "下單→開通→兌換→使用的系統流程卡關，說明存在但系統失敗"),
    ("C-5", "C-1-7", "售後客服互動與政策處理不當，頁面規則已存在"),
    ("C-6", "C-1-6", "旅客主觀期待/自身/外力，商品與執行皆正常"),
]
# 防禦性純正向（10 條）：input_polarity=positive，期望空歸因，僅防禦性統計
DEF_VARIANT_DIST: list[tuple[str, int, str]] = [
    ("direct", 3, "easy"),
    ("colloquial", 3, "easy"),
    ("euphemistic", 2, "medium"),
    ("noisy", 2, "medium"),
]

# ── §5.3：C-1 邊界矩陣——每個 L2 取 3 個可構成 true/false 對照的主要近鄰邊界 ──────
# 選 3 個能構成乾淨 true(頁面問題)/false(責任在他域或無問題) 對照者；「證據不足」型歸入 uncertain 桶。
BOUNDARY_MATRIX: dict[str, list[str]] = {
    "C-1-1": ["C-2", "C-6-3", "no_issue"],  # 客觀品質 / 主觀期待 / 無明確問題
    "C-1-2": ["C-3-3", "C-6-3", "no_issue"],  # 現場偏離表定 / 依表嫌趕 / 頁面已寫清
    "C-1-3": ["C-3-4", "C-3-7", "C-6-2"],  # 現場追加 / 強迫消費 / 單純不值
    "C-1-4": ["C-3-2", "C-6-6", "no_issue"],  # 司機導遊未到 / 自己沒看 / 頁面已寫清
    "C-1-5": ["C-4-1", "C-4-2", "C-6-6"],  # 開通失敗 / 核銷失敗 / 沒看
    "C-1-6": ["C-4-2", "C-3-4", "C-6-6"],  # 資格卡關 / 臨時取消 / 沒讀
    "C-1-7": ["C-5-1", "C-5-2", "C-5-3"],  # 修改未落實 / 退款爭議 / 客服回應
}
PAIRS_PER_BOUNDARY = 3  # 每 (L2, boundary) 3 組 pair
PAIR_VARIANTS = ["direct", "colloquial", "euphemistic"]  # 3 組 pair 各用一種表達

# §5.2：對抗技術池（每個 L2 取 4 種輪替，覆蓋全部）
ADVERSARIAL_TECHNIQUES = [
    "negation_reversal",  # 否定反轉
    "complain_then_clarify",  # 先抱怨後澄清
    "rhetorical",  # 反問
    "sarcasm",  # 讽刺
    "simplified_traditional_mix",  # 簡繁混寫
    "multilingual",  # 多語言
    "emoji",  # emoji
    "typo",  # 錯別字
    "length_extreme",  # 長短文本
    "prompt_injection",  # Prompt Injection
]


def build_layer1() -> Plan:
    """Layer 1（規則單元，130）：正例 70（7 L2×10）+ 負例 50（5 域×10）+ 防禦正向 10。"""
    cells: list[PlanCell] = []
    # 正例 70
    for l2 in C1_L2_CODES:
        for variant, count, diff, pol in POS_VARIANT_DIST:
            cells.append(
                PlanCell(
                    cell_id=f"c1-l1-pos-{l2}-{variant}",
                    domain_under_test="C-1",
                    layer=1,
                    expected_domain="true",
                    focus_l2=l2,
                    target_l2_codes=[l2],
                    boundary_with=None,
                    expression_variant=variant,
                    difficulty=diff,
                    input_polarity=pol,
                    case_family="rule_unit",
                    target_count=count,
                    coverage_note=f"{l2} 正例：{L2_COVERAGE[l2]}",
                )
            )
    # 負例 50（責任明確屬他域，C-1 應回空歸因）
    for dom, focus, note in NEG_DOMAINS:
        for variant, count, diff in NEG_VARIANT_DIST:
            cells.append(
                PlanCell(
                    cell_id=f"c1-l1-neg-{dom}-{variant}",
                    domain_under_test="C-1",
                    layer=1,
                    expected_domain="false",
                    focus_l2=focus,
                    boundary_with=dom,
                    expression_variant=variant,
                    difficulty=diff,
                    input_polarity="negative",
                    case_family="negative_other_domain",
                    target_count=count,
                    coverage_note=f"真實責任={dom}：{note}；不得含獨立成立的 C-1 問題",
                )
            )
    # 防禦性純正向 10（真實鏈路應被極性層攔截，此處僅防禦統計）
    for i, (variant, count, diff) in enumerate(DEF_VARIANT_DIST):
        cells.append(
            PlanCell(
                cell_id=f"c1-l1-def-{variant}",
                domain_under_test="C-1",
                layer=1,
                expected_domain="false",
                focus_l2=C1_L2_CODES[i % len(C1_L2_CODES)],
                boundary_with=None,
                expression_variant=variant,
                difficulty=diff,
                input_polarity="positive",
                case_family="defensive_positive",
                target_count=count,
                coverage_note="純正向/無問題評論；期望空歸因，僅防禦性統計",
            )
        )
    return Plan(
        plan_id="c1-layer1",
        domain_under_test="C-1",
        layer=1,
        description="Layer 1 規則單元測試：正例 70 + 負例 50 + 防禦正向 10 = 130",
        total_target=130,
        cells=cells,
    )


def build_layer2() -> Plan:
    """Layer 2（邊界與對抗，210）：對照 126 + 混合 28 + 不確定 28 + 對抗 28。"""
    cells: list[PlanCell] = []
    # 對照組 126：7 L2 × 3 boundary × 3 pair × 2 條
    for l2 in C1_L2_CODES:
        for boundary in BOUNDARY_MATRIX[l2]:
            for g in range(1, PAIRS_PER_BOUNDARY + 1):
                variant = PAIR_VARIANTS[(g - 1) % len(PAIR_VARIANTS)]
                cells.append(
                    PlanCell(
                        cell_id=f"c1-l2-pair-{l2}-{boundary}-{g}",
                        domain_under_test="C-1",
                        layer=2,
                        expected_domain="pair",
                        focus_l2=l2,
                        target_l2_codes=[l2],  # true 側命中碼
                        boundary_with=boundary,
                        expression_variant=variant,
                        difficulty="hard",
                        input_polarity="negative",
                        case_family="contrast_pair",
                        target_count=2,  # 一格 = 一對（A:true + B:false）
                        pair_group=g,
                        contrast_theme=(
                            f"頁面是否已明確揭露【{L2_COVERAGE[l2]}】——"
                            f"A 側頁面未寫清(C-1 命中 {l2})，B 側責任在 {boundary}；本對僅此一事實不同"
                        ),
                        coverage_note=f"{l2} vs {boundary} 對照（pair {g}）",
                    )
                )
    # 混合評論 28：每 L2 4 條（整體滿意但含具體 C-1 問題點）
    for l2 in C1_L2_CODES:
        cells.append(
            PlanCell(
                cell_id=f"c1-l2-mixed-{l2}",
                domain_under_test="C-1",
                layer=2,
                expected_domain="true",
                focus_l2=l2,
                target_l2_codes=[l2],
                boundary_with=None,
                expression_variant="neutral_mixed",
                difficulty="hard",
                input_polarity="neutral",
                case_family="mixed",
                target_count=4,
                coverage_note=f"{l2} 混合：整體正向+一個明確 {l2} 問題點；被稱讚面向不歸因",
            )
        )
    # 不確定/證據不足 28：每 L2 4 條（測試棄權，不進主二分類分母）
    for l2 in C1_L2_CODES:
        cells.append(
            PlanCell(
                cell_id=f"c1-l2-unc-{l2}",
                domain_under_test="C-1",
                layer=2,
                expected_domain="uncertain",
                focus_l2=l2,
                boundary_with=None,
                expression_variant="ambiguous",
                difficulty="hard",
                input_polarity="negative",
                case_family="uncertain",
                target_count=4,
                coverage_note=(
                    f"{l2} 不確定：無法判斷頁面寫錯還是現場偏離/旅客沒看；"
                    "責任判斷需查看真實商品頁或訂單 → 只能 uncertain"
                ),
            )
        )
    # 對抗與魯棒性 28：每 L2 4 條（真實 C-1 問題被對抗噪聲包裹，標籤仍成立）
    for i, l2 in enumerate(C1_L2_CODES):
        techs = [
            ADVERSARIAL_TECHNIQUES[(i * 4 + k) % len(ADVERSARIAL_TECHNIQUES)]
            for k in range(4)
        ]
        cells.append(
            PlanCell(
                cell_id=f"c1-l2-adv-{l2}",
                domain_under_test="C-1",
                layer=2,
                expected_domain="true",
                focus_l2=l2,
                target_l2_codes=[l2],
                boundary_with=None,
                expression_variant="adversarial",
                difficulty="hard",
                input_polarity="negative",
                case_family="adversarial",
                target_count=4,
                adversarial_techniques=techs,
                coverage_note=(
                    f"{l2} 對抗：真實 C-1 問題以 {', '.join(techs)} 包裝；"
                    "prompt_injection 僅作待判文本，NEVER 執行"
                ),
            )
        )
    return Plan(
        plan_id="c1-layer2",
        domain_under_test="C-1",
        layer=2,
        description="Layer 2 邊界與對抗：對照 126 + 混合 28 + 不確定 28 + 對抗 28 = 210",
        total_target=210,
        cells=cells,
    )


def main() -> None:
    """建構兩層 plan 並寫出 JSON（含 total/cell 數量自檢與摘要）。"""
    out_dir = Path(__file__).resolve().parents[2] / "evals" / "prompt_lab" / "plans"
    out_dir.mkdir(parents=True, exist_ok=True)
    for plan, fname in (
        (build_layer1(), "c1_layer1_plan.json"),
        (build_layer2(), "c1_layer2_plan.json"),
    ):
        path = out_dir / fname
        path.write_text(
            json.dumps(plan.model_dump(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            f"✅ {fname}: {len(plan.cells)} 格, total_target={plan.total_target}, sum={sum(c.target_count for c in plan.cells)} → {path}"
        )


if __name__ == "__main__":
    main()
