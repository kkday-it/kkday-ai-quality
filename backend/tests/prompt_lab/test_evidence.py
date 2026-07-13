"""證據逐字子串 + grounding 指標 + 去重正規化（PRD §11.3/§7/§19）。"""

from __future__ import annotations

import metrics as M
import schemas as S


def test_verbatim_grounded_substring():
    assert S.verbatim_grounded("沒說明時長", "頁面沒說明時長問題")
    assert not S.verbatim_grounded("沒說明費用", "頁面沒說明時長問題")


def test_verbatim_grounded_empty_is_false():
    assert not S.verbatim_grounded("", "任何文字")  # 空 quote 不算落地


def test_verbatim_grounded_no_normalization():
    # 逐字判準：全形/半形不同視為不落地（grounding 要求原文逐字）
    assert not S.verbatim_grounded("ABC", "Ａ Ｂ Ｃ")


def test_normalize_for_dedup_nfkc_and_whitespace():
    assert S.normalize_for_dedup("  頁面　 只寫\t半日 ") == "頁面 只寫 半日"
    # NFKC：全形英數 → 半形；去重視為同一
    assert S.normalize_for_dedup("ＡＢＣ１２３") == "ABC123"


def _c(cid, text, ev):
    return {
        "case_id": cid,
        "domain_under_test": "C-1",
        "layer": 1,
        "text": text,
        "input_polarity": "negative",
        "expected_domain": "true",
        "expected_l2_codes": ["C-1-2"],
        "forbidden_l2_codes": [],
        "expected_evidence_quotes": ev,
        "case_family": "rule_unit",
        "expression_variant": "direct",
        "difficulty": "easy",
        "language": "zh-tw",
        "boundary_with": None,
        "contrast_pair_id": None,
        "contrast_key": None,
        "label_reason": "",
        "origin": "ai_generated",
    }


def _r(cid, hit, ev):
    return {
        "run_id": "r",
        "case_id": cid,
        "repeat_index": 0,
        "prompt_version": "v",
        "prompt_sha256": "x",
        "model": "m",
        "predicted_domain_hit": hit,
        "predicted_l2_codes": ["C-1-2"] if hit else [],
        "predicted_evidence_quotes": ev,
        "predicted_confidences": [0.8],
        "schema_valid": True,
        "attempts": 1,
    }


def test_evidence_metrics_grounding_and_empty():
    cases = [
        _c("a", "頁面沒說明時長", ["沒說明時長"]),
        _c("b", "頁面沒寫門票", ["沒寫門票"]),
        _c("c", "頁面很模糊", ["沒說明時長"]),
    ]
    results = [
        _r("a", True, ["沒說明時長"]),  # grounded
        _r("b", True, ["這句不在原文"]),  # 非逐字 → 不 grounded
        _r("c", True, []),  # 命中但無證據 → empty
    ]
    e = M.compute_all(cases, results)["evidence"]
    assert e["n_hit_runs"] == 3
    assert e["empty_evidence_rate"] == round(1 / 3, 4)
    # 非空的 2 條中 1 條逐字 → run grounding 0.5
    assert e["grounding_run_rate"] == 0.5
    assert e["grounding_quote_rate"] == 0.5


def test_evidence_expected_overlap():
    cases = [_c("a", "頁面沒說明時長問題", ["沒說明時長"])]
    results = [_r("a", True, ["沒說明時長"])]
    e = M.compute_all(cases, results)["evidence"]
    assert e["expected_overlap_rate"] == 1.0
