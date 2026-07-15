"""四類資料 Schema + Plan + strict IO schema 鏡像 + L2 SSOT 對齊（PRD §6/§16/§19）。"""

from __future__ import annotations

import json

import pytest
import schemas as S
from pydantic import ValidationError


def _cand(**kw):
    base = dict(
        case_id="c1-x-001",
        domain_under_test="C-1",
        layer=1,
        text="頁面沒說明時長問題很困擾",
        input_polarity="negative",
        expected_domain="true",
        expected_l2_codes=["C-1-2"],
        expected_evidence_quotes=["沒說明時長"],
        case_family="rule_unit",
        expression_variant="direct",
        difficulty="easy",
        language="zh-tw",
    )
    base.update(kw)
    return S.CandidateCase(**base)


def test_candidate_valid_true_false_uncertain():
    assert _cand().expected_domain == "true"
    assert (
        _cand(
            expected_domain="false", expected_l2_codes=[], expected_evidence_quotes=[]
        ).expected_l2_codes
        == []
    )
    assert (
        _cand(
            expected_domain="uncertain", expected_l2_codes=[], expected_evidence_quotes=[]
        ).expected_domain
        == "uncertain"
    )


def test_candidate_true_needs_1_to_2_l2():
    with pytest.raises(ValidationError):
        _cand(expected_l2_codes=[])
    with pytest.raises(ValidationError):
        _cand(expected_l2_codes=["C-1-1", "C-1-2", "C-1-3"])
    assert _cand(expected_l2_codes=["C-1-1", "C-1-2"])


def test_candidate_non_true_forbids_l2():
    with pytest.raises(ValidationError):
        _cand(expected_domain="false", expected_l2_codes=["C-1-1"], expected_evidence_quotes=[])


def test_candidate_evidence_must_be_verbatim():
    with pytest.raises(ValidationError):
        _cand(expected_evidence_quotes=["不存在於原文的字串"])
    with pytest.raises(ValidationError):
        _cand(expected_evidence_quotes=[])


def test_candidate_bad_l2_code_rejected():
    with pytest.raises(ValidationError):
        _cand(expected_l2_codes=["C-9-9"])


def test_candidate_contrast_pair_both_or_neither():
    with pytest.raises(ValidationError):
        _cand(contrast_pair_id="p1")  # 缺 contrast_key
    assert _cand(contrast_pair_id="p1", contrast_key="頁面是否說明時長")


def test_candidate_extra_field_forbidden():
    with pytest.raises(ValidationError):
        S.CandidateCase(
            case_id="x",
            domain_under_test="C-1",
            layer=1,
            text="t",
            input_polarity="negative",
            expected_domain="false",
            case_family="f",
            expression_variant="v",
            difficulty="easy",
            language="zh-tw",
            bogus_field=1,
        )


def test_audit_result_and_bad_l2():
    a = S.AuditResult(
        case_id="c",
        label_supported=True,
        ambiguous=False,
        self_contained=True,
        contains_independent_c1_issue=False,
        suggested_domain="true",
        suggested_l2_codes=["C-1-2"],
        evidence_quotes_valid=True,
        near_duplicate=False,
    )
    assert a.status == "review_required"
    with pytest.raises(ValidationError):
        S.AuditResult(
            case_id="c",
            label_supported=True,
            ambiguous=False,
            self_contained=True,
            contains_independent_c1_issue=False,
            suggested_domain="true",
            suggested_l2_codes=["C-2-1"],
            evidence_quotes_valid=True,
            near_duplicate=False,
        )


def test_frozen_case_valid_and_invariants():
    fc = S.FrozenCase(
        case_id="c",
        domain_under_test="C-1",
        layer=1,
        text="頁面沒寫門票費用",
        input_polarity="negative",
        expected_domain="true",
        expected_l2_codes=["C-1-3"],
        expected_evidence_quotes=["沒寫門票費用"],
        case_family="rule_unit",
        expression_variant="direct",
        difficulty="easy",
        language="zh-tw",
        origin="ai_generated",
        label_supported=True,
        evidence_quotes_valid=True,
        dataset_version="c1-v1",
        split="dev",
        human_reviewed=True,
    )
    assert fc.split == "dev"
    with pytest.raises(ValidationError):
        S.FrozenCase(
            case_id="c",
            domain_under_test="C-1",
            layer=1,
            text="t",
            input_polarity="negative",
            expected_domain="true",
            expected_l2_codes=[],
            case_family="f",
            expression_variant="d",
            difficulty="easy",
            language="zh-tw",
            origin="ai_generated",
            label_supported=True,
            evidence_quotes_valid=True,
            dataset_version="v",
            split="dev",
            human_reviewed=False,
        )


def test_judge_run_result_record():
    r = S.JudgeRunResult(
        run_id="r",
        case_id="c",
        repeat_index=0,
        prompt_version="v",
        prompt_sha256="abc",
        model="m",
        schema_valid=True,
        predicted_domain_hit=True,
    )
    assert r.error is None and r.predicted_domain_hit is True


def test_plan_total_must_match_and_unique_cells():
    cell = S.PlanCell(
        cell_id="a",
        domain_under_test="C-1",
        layer=1,
        expected_domain="true",
        focus_l2="C-1-1",
        target_l2_codes=["C-1-1"],
        expression_variant="direct",
        difficulty="easy",
        input_polarity="negative",
        case_family="rule_unit",
        target_count=3,
    )
    with pytest.raises(ValidationError):
        S.Plan(plan_id="p", domain_under_test="C-1", layer=1, total_target=99, cells=[cell])
    with pytest.raises(ValidationError):
        S.Plan(
            plan_id="p", domain_under_test="C-1", layer=1, total_target=6, cells=[cell, cell]
        )  # dup id


def test_pair_cell_requires_pair_group_and_theme():
    with pytest.raises(ValidationError):
        S.PlanCell(
            cell_id="a",
            domain_under_test="C-1",
            layer=2,
            expected_domain="pair",
            focus_l2="C-1-1",
            target_l2_codes=["C-1-1"],
            expression_variant="direct",
            difficulty="hard",
            input_polarity="negative",
            case_family="contrast_pair",
            target_count=2,
        )


def test_plans_on_disk_are_130_and_210(plans_dir):
    l1 = S.Plan(**json.loads((plans_dir / "c1_layer1_plan.json").read_text(encoding="utf-8")))
    l2 = S.Plan(**json.loads((plans_dir / "c1_layer2_plan.json").read_text(encoding="utf-8")))
    assert sum(c.target_count for c in l1.cells) == 130
    assert sum(c.target_count for c in l2.cells) == 210
    # 正例每 L2 恰 10
    from collections import defaultdict

    pos = defaultdict(int)
    for c in l1.cells:
        if c.case_family == "rule_unit":
            pos[c.focus_l2] += c.target_count
    assert all(v == 10 for v in pos.values()) and len(pos) == 7


def test_strict_io_schemas_valid_and_mirror_pydantic():
    from jsonschema import Draft202012Validator

    Draft202012Validator.check_schema(S.GENERATOR_OUTPUT_SCHEMA)
    Draft202012Validator.check_schema(S.AUDITOR_OUTPUT_SCHEMA)
    assert set(S.AUDITOR_OUTPUT_SCHEMA["properties"]) == set(S.AuditorOutput.model_fields)
    gen_item = S.GENERATOR_OUTPUT_SCHEMA["properties"]["cases"]["items"]["properties"]
    assert set(gen_item) == set(S.GeneratorCaseOut.model_fields)


def test_c1_l2_codes_match_prompt_schema_enum(c1_prompt_path):
    """SSOT 守衛：C1_L2_CODES 必須等於 baseline C-1 prompt 的 schema enum。"""
    import prompt_parser as pp

    parsed = pp.parse_prompt_file(c1_prompt_path)
    assert tuple(parsed.schema_l2_enum()) == S.C1_L2_CODES


def test_prompt_manifest_matches_files(prompts_dir):
    """追溯性守衛（§12）：manifest 記錄的 SHA-256 必須與實際 prompt 檔一致。"""
    import hashlib

    manifest = json.loads((prompts_dir / "prompts_manifest.json").read_text(encoding="utf-8"))
    # 7 份域 baseline judge（polarity + C-1..C-6）恆在；候選 prompt（如 01_C-1_content_v2）可另加入。
    baseline_judges = {
        "00_polarity",
        "01_C-1_content",
        "02_C-2_quality",
        "03_C-3_supplier",
        "04_C-4_platform",
        "05_C-5_service",
        "06_C-6_customer",
    }
    assert baseline_judges <= set(manifest["judges"])
    for entry in list(manifest["judges"].values()) + list(manifest["generators"].values()):
        actual = hashlib.sha256((prompts_dir.parent / entry["path"]).read_bytes()).hexdigest()
        assert actual == entry["sha256"], entry["path"]
