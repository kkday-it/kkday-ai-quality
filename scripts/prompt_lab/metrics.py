"""C-1 評測指標（PRD §11）——純函式，可對 fixture 精確斷言（Phase 3 DoD）。

輸入 = FrozenCase gold + 每 case 多個 JudgeRunResult（repeats）。指標在「所有 (case,repeat) run」
上池化計算，不做多數投票掩蓋不穩定（§10.4）；穩定性另以 repeat 間一致率衡量（§11.4）。

零 API、零 backend 依賴。所有核心指標可按 §11.6 維度切片。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from schemas import C1_L2_CODES, verbatim_grounded

# 可切片維度（§11.6）
SLICE_DIMS = [
    "layer",
    "expected_domain",
    "primary_l2",
    "boundary_with",
    "case_family",
    "expression_variant",
    "difficulty",
    "input_polarity",
    "language",
    "origin",
    "split",
]


def _safe(n: float, d: float) -> float | None:
    """安全除法：分母 0 回 None（指標無定義，報告據此標 n/a）。"""
    return round(n / d, 4) if d else None


@dataclass
class EvalRow:
    """一個 (case, repeat) 展平列：gold 標籤 + 該次預測 + 切片維度。"""

    case_id: str
    repeat_index: int
    expected_domain: str
    gold_hit: bool  # expected_domain == "true"
    expected_l2: list[str]
    pred_hit: bool
    pred_l2: list[str]
    pred_evidence: list[str]
    expected_evidence: list[str]
    confidence: float | None
    schema_valid: bool
    text: str
    contrast_pair_id: str | None
    contrast_key: str | None
    layer: int = 1
    primary_l2: str = ""
    boundary_with: str = ""
    case_family: str = ""
    expression_variant: str = ""
    difficulty: str = ""
    input_polarity: str = ""
    language: str = ""
    origin: str = ""
    split: str = ""

    def slice_value(self, dim: str) -> str:
        return str(getattr(self, dim, ""))


def build_rows(cases: list[dict], results: list[dict]) -> list[EvalRow]:
    """把 FrozenCase dict 與 JudgeRunResult dict 連接成 EvalRow 清單（依 case_id join）。"""
    by_case: dict[str, dict] = {c["case_id"]: c for c in cases}
    rows: list[EvalRow] = []
    for r in results:
        c = by_case.get(r["case_id"])
        if c is None:
            continue
        exp_l2 = c.get("expected_l2_codes", [])
        primary = (
            exp_l2[0]
            if (c["expected_domain"] == "true" and exp_l2)
            else (c.get("boundary_with") or c["expected_domain"])
        )
        confs = r.get("predicted_confidences") or []
        rows.append(
            EvalRow(
                case_id=r["case_id"],
                repeat_index=r.get("repeat_index", 0),
                expected_domain=c["expected_domain"],
                gold_hit=(c["expected_domain"] == "true"),
                expected_l2=exp_l2,
                pred_hit=bool(r.get("predicted_domain_hit")),
                pred_l2=list(r.get("predicted_l2_codes") or []),
                pred_evidence=list(r.get("predicted_evidence_quotes") or []),
                expected_evidence=list(c.get("expected_evidence_quotes") or []),
                confidence=(max(confs) if confs else None),
                schema_valid=bool(r.get("schema_valid")),
                text=c.get("text", ""),
                contrast_pair_id=c.get("contrast_pair_id"),
                contrast_key=c.get("contrast_key"),
                layer=c.get("layer", 1),
                primary_l2=str(primary),
                boundary_with=str(c.get("boundary_with") or ""),
                case_family=c.get("case_family", ""),
                expression_variant=c.get("expression_variant", ""),
                difficulty=c.get("difficulty", ""),
                input_polarity=c.get("input_polarity", ""),
                language=c.get("language", ""),
                origin=c.get("origin", ""),
                split=c.get("split", ""),
            )
        )
    return rows


# ── §11.1 域二分類（只用 expected true/false；uncertain 不進分母）──────────────────
def domain_metrics(rows: list[EvalRow], *, exclude_defensive: bool = True) -> dict:
    """Precision/Recall/Specificity/F1/FPR/FNR（純正向防禦樣本預設排除，另計）。"""
    tp = fp = tn = fn = 0
    for r in rows:
        if r.expected_domain == "uncertain":
            continue
        if exclude_defensive and r.case_family == "defensive_positive":
            continue
        if r.gold_hit and r.pred_hit:
            tp += 1
        elif r.gold_hit and not r.pred_hit:
            fn += 1
        elif not r.gold_hit and r.pred_hit:
            fp += 1
        else:
            tn += 1
    p, rec = _safe(tp, tp + fp), _safe(tp, tp + fn)
    f1 = (
        _safe(2 * (p or 0) * (rec or 0), (p or 0) + (rec or 0))
        if (p and rec)
        else (0.0 if (tp + fp + fn) else None)
    )
    return {
        "n": tp + fp + tn + fn,
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "precision": p,
        "recall": rec,
        "specificity": _safe(tn, tn + fp),
        "f1": f1,
        "fpr": _safe(fp, fp + tn),
        "fnr": _safe(fn, fn + tp),
    }


def defensive_metrics(rows: list[EvalRow]) -> dict:
    """純正向防禦樣本獨立統計（§11.1）：應回空歸因，統計誤命中率。"""
    d = [r for r in rows if r.case_family == "defensive_positive"]
    fp = sum(1 for r in d if r.pred_hit)
    return {
        "n": len(d),
        "false_hit": fp,
        "false_hit_rate": _safe(fp, len(d)),
        "stay_empty_rate": _safe(len(d) - fp, len(d)),
    }


# ── §11.2 L2（只對 expected true）──────────────────────────────────────────────
def l2_metrics(rows: list[EvalRow]) -> dict:
    """Exact Set/Any-hit Accuracy、per-L2 P/R/F1、over/under-attribution、duplicate rate。"""
    tr = [r for r in rows if r.gold_hit]
    if not tr:
        return {"n": 0}
    exact = sum(1 for r in tr if set(r.pred_l2) == set(r.expected_l2))
    anyhit = sum(1 for r in tr if set(r.pred_l2) & set(r.expected_l2))
    over = sum(1 for r in tr if set(r.pred_l2) - set(r.expected_l2))
    under = sum(1 for r in tr if set(r.expected_l2) - set(r.pred_l2))
    dup = sum(1 for r in tr if len(r.pred_l2) != len(set(r.pred_l2)))
    per_l2: dict[str, dict] = {}
    for code in C1_L2_CODES:
        c_tp = sum(1 for r in tr if code in r.pred_l2 and code in r.expected_l2)
        c_fp = sum(1 for r in tr if code in r.pred_l2 and code not in r.expected_l2)
        c_fn = sum(1 for r in tr if code not in r.pred_l2 and code in r.expected_l2)
        pp, rr = _safe(c_tp, c_tp + c_fp), _safe(c_tp, c_tp + c_fn)
        per_l2[code] = {
            "tp": c_tp,
            "fp": c_fp,
            "fn": c_fn,
            "precision": pp,
            "recall": rr,
            "f1": _safe(2 * (pp or 0) * (rr or 0), (pp or 0) + (rr or 0))
            if (pp and rr)
            else None,
        }
    return {
        "n": len(tr),
        "exact_set_accuracy": _safe(exact, len(tr)),
        "any_hit_accuracy": _safe(anyhit, len(tr)),
        "over_attribution_rate": _safe(over, len(tr)),
        "under_attribution_rate": _safe(under, len(tr)),
        "duplicate_rate": _safe(dup, len(tr)),
        "per_l2": per_l2,
    }


# ── §11.3 證據 ─────────────────────────────────────────────────────────────────
def evidence_metrics(rows: list[EvalRow]) -> dict:
    """Evidence Grounding（逐字子串）、Expected Overlap、Empty Evidence Rate。"""
    hits = [r for r in rows if r.pred_hit]  # 有歸因才談證據
    n_hit = len(hits)
    grounded_runs = 0
    total_q = grounded_q = 0
    empty = 0
    for r in hits:
        if not r.pred_evidence:
            empty += 1
            continue
        ok = all(verbatim_grounded(q, r.text) for q in r.pred_evidence)
        grounded_runs += 1 if ok else 0
        total_q += len(r.pred_evidence)
        grounded_q += sum(1 for q in r.pred_evidence if verbatim_grounded(q, r.text))
    # Expected overlap：true case 中，預測證據與期望證據有交疊（互為子串）
    tr = [r for r in rows if r.gold_hit and r.pred_evidence and r.expected_evidence]
    overlap = sum(
        1 for r in tr if _quotes_overlap(r.pred_evidence, r.expected_evidence)
    )
    return {
        "n_hit_runs": n_hit,
        "grounding_run_rate": _safe(grounded_runs, n_hit - empty)
        if (n_hit - empty)
        else None,
        "grounding_quote_rate": _safe(grounded_q, total_q),
        "empty_evidence_rate": _safe(empty, n_hit),
        "expected_overlap_rate": _safe(overlap, len(tr)) if tr else None,
    }


def _quotes_overlap(pred: list[str], expected: list[str]) -> bool:
    """任一預測 quote 與任一期望 quote 互為子串（寬鬆重疊判準）。"""
    for p in pred:
        for e in expected:
            if p and e and (p in e or e in p):
                return True
    return False


# ── §11.4 穩定性（repeat 間）──────────────────────────────────────────────────
def stability_metrics(rows: list[EvalRow]) -> dict:
    """Domain/L2 Full Agreement、Pairwise Agreement、confidence range/std、flip cases。"""
    by_case: dict[str, list[EvalRow]] = defaultdict(list)
    for r in rows:
        by_case[r.case_id].append(r)
    multi = {cid: rs for cid, rs in by_case.items() if len(rs) >= 2}
    dom_full = sum(1 for rs in multi.values() if len({r.pred_hit for r in rs}) == 1)
    true_cases = {cid: rs for cid, rs in multi.items() if rs[0].gold_hit}
    l2_full = sum(
        1
        for rs in true_cases.values()
        if len({tuple(sorted(r.pred_l2)) for r in rs}) == 1
    )
    # pairwise agreement（domain）：每 case 所有 repeat 兩兩相等比例平均
    pair_agrees = []
    flips = []
    for cid, rs in multi.items():
        preds = [r.pred_hit for r in rs]
        agree = sum(
            1
            for i in range(len(preds))
            for j in range(i + 1, len(preds))
            if preds[i] == preds[j]
        )
        total = len(preds) * (len(preds) - 1) // 2
        pair_agrees.append(agree / total if total else 1.0)
        if len(set(preds)) > 1:
            flips.append(cid)
    confs = [r.confidence for r in rows if r.confidence is not None]
    conf_std = None
    if len(confs) >= 2:
        mean = sum(confs) / len(confs)
        conf_std = round((sum((c - mean) ** 2 for c in confs) / len(confs)) ** 0.5, 4)
    return {
        "n_cases_multi_repeat": len(multi),
        "domain_full_agreement": _safe(dom_full, len(multi)),
        "l2_set_full_agreement": _safe(l2_full, len(true_cases))
        if true_cases
        else None,
        "pairwise_agreement": round(sum(pair_agrees) / len(pair_agrees), 4)
        if pair_agrees
        else None,
        "confidence_range": [round(min(confs), 4), round(max(confs), 4)]
        if confs
        else None,
        "confidence_std": conf_std,
        "flip_cases": sorted(flips),
    }


# ── §11.5 Uncertain 與 contrast pair ───────────────────────────────────────────
def uncertain_metrics(rows: list[EvalRow]) -> dict:
    """Abstain Rate、Forced Attribution Rate、被強制歸入的 L2 分佈。"""
    unc = [r for r in rows if r.expected_domain == "uncertain"]
    if not unc:
        return {"n": 0}
    abstain = sum(1 for r in unc if not r.pred_hit)
    forced = [r for r in unc if r.pred_hit]
    dist: dict[str, int] = defaultdict(int)
    for r in forced:
        for code in r.pred_l2:
            dist[code] += 1
    return {
        "n": len(unc),
        "abstain_rate": _safe(abstain, len(unc)),
        "forced_attribution_rate": _safe(len(forced), len(unc)),
        "forced_l2_distribution": dict(sorted(dist.items())),
    }


def contrast_metrics(rows: list[EvalRow]) -> dict:
    """Pair Both Correct、正側/負側 accuracy、按 contrast_key 的失敗分佈。"""
    pairs: dict[str, dict[str, list[EvalRow]]] = defaultdict(
        lambda: {"true": [], "false": []}
    )
    for r in rows:
        if r.contrast_pair_id:
            pairs[r.contrast_pair_id]["true" if r.gold_hit else "false"].append(r)
    if not pairs:
        return {"n_pairs": 0}
    both_correct = 0
    pos_correct = pos_total = neg_correct = neg_total = 0
    fail_by_key: dict[str, int] = defaultdict(int)
    n_eval_pairs = 0
    for pid, sides in pairs.items():
        if not sides["true"] or not sides["false"]:
            continue  # 不完整 pair（跨 split 或缺側）不計
        n_eval_pairs += 1
        # 以每側「多數 repeat 正確」定義該側正確（穩定看 §11.4，此處要 pair 綜合）
        pos_ok = (
            sum(1 for r in sides["true"] if r.pred_hit) >= (len(sides["true"]) + 1) // 2
        )
        neg_ok = (
            sum(1 for r in sides["false"] if not r.pred_hit)
            >= (len(sides["false"]) + 1) // 2
        )
        pos_total += 1
        neg_total += 1
        pos_correct += 1 if pos_ok else 0
        neg_correct += 1 if neg_ok else 0
        if pos_ok and neg_ok:
            both_correct += 1
        else:
            key = (sides["true"] or sides["false"])[0].contrast_key or "(unknown)"
            fail_by_key[key] += 1
    return {
        "n_pairs": n_eval_pairs,
        "pair_both_correct_rate": _safe(both_correct, n_eval_pairs),
        "positive_side_accuracy": _safe(pos_correct, pos_total),
        "negative_side_accuracy": _safe(neg_correct, neg_total),
        "failure_by_contrast_key": dict(
            sorted(fail_by_key.items(), key=lambda kv: -kv[1])
        ),
    }


# ── §11.6 切片 ─────────────────────────────────────────────────────────────────
def sliced(rows: list[EvalRow], dim: str, metric_fn) -> dict:
    """把 rows 按某維度分組，各組套用 metric_fn（如 domain_metrics）。"""
    groups: dict[str, list[EvalRow]] = defaultdict(list)
    for r in rows:
        groups[r.slice_value(dim)].append(r)
    return {val: metric_fn(rs) for val, rs in sorted(groups.items())}


def compute_all(cases: list[dict], results: list[dict]) -> dict:
    """一次算出全部指標區塊（供 report.py / metrics.json）。"""
    rows = build_rows(cases, results)
    schema_valid_rate = _safe(
        sum(1 for r in results if r.get("schema_valid")), len(results)
    )
    return {
        "n_cases": len({r["case_id"] for r in results}),
        "n_runs": len(results),
        "schema_valid_rate": schema_valid_rate,
        "domain": domain_metrics(rows),
        "defensive": defensive_metrics(rows),
        "l2": l2_metrics(rows),
        "evidence": evidence_metrics(rows),
        "stability": stability_metrics(rows),
        "uncertain": uncertain_metrics(rows),
        "contrast": contrast_metrics(rows),
        "slices": {dim: sliced(rows, dim, domain_metrics) for dim in SLICE_DIMS},
    }
