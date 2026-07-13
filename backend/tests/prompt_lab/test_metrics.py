"""指標精確性 + true/false/uncertain 分母 + L2 exact/extra/missing + 報告 fixture（PRD §11/§19）。"""

from __future__ import annotations

import metrics as M


def _case(cid, dom, l2, text, fam, ev=None, boundary=None, pair=None, key=None, pol="negative"):
    return {
        "case_id": cid,
        "domain_under_test": "C-1",
        "layer": 1,
        "text": text,
        "input_polarity": pol,
        "expected_domain": dom,
        "expected_l2_codes": l2,
        "forbidden_l2_codes": [],
        "expected_evidence_quotes": ev or [],
        "case_family": fam,
        "expression_variant": "direct",
        "difficulty": "easy",
        "language": "zh-tw",
        "boundary_with": boundary,
        "contrast_pair_id": pair,
        "contrast_key": key,
        "label_reason": "",
        "origin": "ai_generated",
    }


def _run(cid, rep, hit, l2=None, ev=None, conf=0.8):
    return {
        "run_id": "r",
        "case_id": cid,
        "repeat_index": rep,
        "prompt_version": "v",
        "prompt_sha256": "x",
        "model": "m",
        "predicted_domain_hit": hit,
        "predicted_l2_codes": l2 or [],
        "predicted_evidence_quotes": ev or [],
        "predicted_confidences": [conf],
        "schema_valid": True,
        "attempts": 1,
    }


def _fixture():
    cases = [
        _case("t1", "true", ["C-1-2"], "頁面沒說明時長問題", "rule_unit", ["沒說明時長"]),
        _case("t2", "true", ["C-1-3"], "沒寫門票費用啦", "rule_unit", ["沒寫門票費用"]),
        _case("f1", "false", [], "現場導遊遲到很久", "negative_other_domain", boundary="C-3"),
        _case("f2", "false", [], "很棒的行程推薦", "defensive_positive"),
        _case("u1", "uncertain", [], "時間跟描述不一樣", "uncertain"),
        _case(
            "pa",
            "true",
            ["C-1-2"],
            "頁面沒寫幾點集合",
            "contrast_pair",
            ["沒寫幾點集合"],
            boundary="C-3",
            pair="P1",
            key="集合說明",
        ),
        _case(
            "pb",
            "false",
            [],
            "頁面寫清楚但現場遲到",
            "contrast_pair",
            boundary="C-3",
            pair="P1",
            key="集合說明",
        ),
    ]
    results = [
        _run("t1", 0, True, ["C-1-2"], ["沒說明時長"], 0.9),
        _run("t1", 1, True, ["C-1-2"], ["沒說明時長"]),
        _run("t2", 0, True, ["C-1-3"], ["沒寫門票費用"]),
        _run("t2", 1, False, [], []),
        _run("f1", 0, False),
        _run("f1", 1, True, [], []),
        _run("f2", 0, False),
        _run("f2", 1, True, [], []),
        _run("u1", 0, False),
        _run("u1", 1, True, ["C-1-1"], []),
        _run("pa", 0, True, ["C-1-2"], ["沒寫幾點集合"]),
        _run("pa", 1, True, ["C-1-2"], ["沒寫幾點集合"]),
        _run("pb", 0, False),
        _run("pb", 1, False),
    ]
    return cases, results


def test_domain_binary_exact():
    a = M.compute_all(*_fixture())
    d = a["domain"]
    assert (d["n"], d["tp"], d["fp"], d["tn"], d["fn"]) == (10, 5, 1, 3, 1)
    assert d["precision"] == 0.8333 and d["recall"] == 0.8333
    assert d["specificity"] == 0.75 and d["fpr"] == 0.25 and d["fnr"] == 0.1667


def test_uncertain_excluded_from_domain_denominator():
    a = M.compute_all(*_fixture())
    # uncertain(u1) 與 defensive(f2) 不進域分母：n=10（否則會是 14）
    assert a["domain"]["n"] == 10
    assert a["defensive"]["n"] == 2 and a["defensive"]["false_hit"] == 1


def test_l2_exact_extra_missing():
    a = M.compute_all(*_fixture())["l2"]
    assert a["n"] == 6
    assert a["exact_set_accuracy"] == 0.8333 and a["any_hit_accuracy"] == 0.8333
    assert a["under_attribution_rate"] == 0.1667 and a["over_attribution_rate"] == 0.0
    assert a["per_l2"]["C-1-2"]["f1"] == 1.0 and a["per_l2"]["C-1-3"]["recall"] == 0.5


def test_l2_over_attribution_detected():
    cases = [_case("t", "true", ["C-1-2"], "頁面沒說明時長", "rule_unit", ["沒說明時長"])]
    results = [_run("t", 0, True, ["C-1-2", "C-1-5"], ["沒說明時長"])]  # 多吐 C-1-5
    a = M.compute_all(cases, results)["l2"]
    assert (
        a["over_attribution_rate"] == 1.0
        and a["exact_set_accuracy"] == 0.0
        and a["any_hit_accuracy"] == 1.0
    )


def test_stability_flip_and_agreement():
    s = M.compute_all(*_fixture())["stability"]
    assert s["domain_full_agreement"] == 0.4286
    assert s["l2_set_full_agreement"] == 0.6667
    assert s["flip_cases"] == ["f1", "f2", "t2", "u1"]
    assert s["confidence_range"] == [0.8, 0.9]


def test_uncertain_and_contrast():
    a = M.compute_all(*_fixture())
    u = a["uncertain"]
    assert u["n"] == 2 and u["abstain_rate"] == 0.5 and u["forced_attribution_rate"] == 0.5
    assert u["forced_l2_distribution"] == {"C-1-1": 1}
    c = a["contrast"]
    assert c["n_pairs"] == 1 and c["pair_both_correct_rate"] == 1.0


def test_slices_present():
    a = M.compute_all(*_fixture())
    assert set(a["slices"]["expected_domain"]) == {"true", "false", "uncertain"}
    assert "C-3" in a["slices"]["boundary_with"]


def test_report_fixture_writes_all_files(tmp_path):
    import report

    cases, results = _fixture()
    report.write_reports(tmp_path, cases, results, {"run_id": "t", "n_runs": len(results)})
    for f in [
        "metrics.json",
        "summary.md",
        "errors.csv",
        "unstable_cases.csv",
        "boundary_matrix.csv",
        "contrast_pairs.csv",
    ]:
        assert (tmp_path / f).exists()
    import json

    m = json.loads((tmp_path / "metrics.json").read_text(encoding="utf-8"))
    assert "gates" in m and "metrics" in m
