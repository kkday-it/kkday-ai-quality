"""C-2 plan、schema 与 Generator/Auditor 自动路由（fake client，零 API）。"""

from __future__ import annotations

import json

import audit_cases as audit
import generate_cases as gen
import prompt_parser as pp
import pytest
import schemas as S
from fake_client import FakeResponsesClient
from openai_gateway import Gateway
from pydantic import ValidationError


def _gw(responder):
    return Gateway(client=FakeResponsesClient(responder), sleep=lambda _: None)


def test_c2_candidate_accepts_own_l2_and_rejects_cross_domain():
    base = dict(
        case_id="c2-x-001",
        domain_under_test="C-2",
        layer=1,
        text="eSIM 已启用，但在市区每三分钟就断线",
        input_polarity="negative",
        expected_domain="true",
        expected_l2_codes=["C-2-1"],
        expected_evidence_quotes=["每三分钟就断线"],
        case_family="rule_unit",
        expression_variant="direct",
        difficulty="easy",
        language="zh-tw",
    )
    assert S.CandidateCase(**base).expected_l2_codes == ["C-2-1"]
    with pytest.raises(ValidationError):
        S.CandidateCase(**{**base, "expected_l2_codes": ["C-1-1"]})


def test_c2_plans_and_prompt_schema(plans_dir, prompts_dir):
    layer1 = S.Plan(
        **json.loads((plans_dir / "c2_layer1_plan.json").read_text(encoding="utf-8"))
    )
    layer2 = S.Plan(
        **json.loads((plans_dir / "c2_layer2_plan.json").read_text(encoding="utf-8"))
    )
    assert (len(layer1.cells), layer1.total_target) == (54, 110)
    assert (len(layer2.cells), layer2.total_target) == (60, 150)
    positives = {code: 0 for code in S.C2_L2_CODES}
    for cell in layer1.cells:
        if cell.case_family == "rule_unit":
            positives[cell.focus_l2] += cell.target_count
    assert positives == {code: 10 for code in S.C2_L2_CODES}

    judge = pp.parse_prompt_file(prompts_dir / "judges" / "02_C-2_quality.md")
    assert tuple(judge.schema_l2_enum()) == S.C2_L2_CODES


def test_c2_generator_and_auditor_route_with_fake_model(plans_dir):
    plan = S.Plan(
        **json.loads((plans_dir / "c2_layer1_plan.json").read_text(encoding="utf-8"))
    )
    cell = plan.cells[0]
    gen_prompt = pp.parse_gen_prompt_file(gen.generator_prompt_path("C-2"))

    def generator_reply(system, user, schema_name, schema, _n):
        assert "<c2_label_contract>" in system
        assert schema_name == "c2_generator_output"
        assert "可观察" in user
        return {
            "cases": [
                {
                    "text": f"样本{i}：eSIM 已启用，但在市区每三分钟就断线。",
                    "evidence_quotes": ["在市区每三分钟就断线"],
                    "label_reason": "启用后的断线属于网络品质。",
                    "language": "zh-tw",
                    "pair_side": None,
                }
                for i in range(3)
            ]
        }

    cases, error = gen.process_cell(_gw(generator_reply), gen_prompt, cell, "fake-gen")
    assert error is None and len(cases) == 3
    assert all(c.domain_under_test == "C-2" for c in cases)
    assert all(c.expected_l2_codes == ["C-2-1"] for c in cases)

    audit_prompt = pp.parse_gen_prompt_file(audit.auditor_prompt_path("C-2"))

    def auditor_reply(system, _user, schema_name, schema, _n):
        assert "<c2_label_contract>" in system
        assert schema_name == "c2_auditor_output"
        assert "contains_independent_c2_issue" in schema["properties"]
        return {
            "label_supported": True,
            "ambiguous": False,
            "self_contained": True,
            "contains_independent_c2_issue": False,
            "suggested_domain": "true",
            "suggested_l2_codes": ["C-2-1"],
            "evidence_quotes_valid": True,
            "near_duplicate": False,
            "audit_reason": "启用后断线的证据完整。",
        }

    result, error = audit.audit_one(
        _gw(auditor_reply), audit_prompt, cases[0], "fake-auditor"
    )
    assert error is None
    assert result["domain_under_test"] == "C-2"
    assert result["suggested_l2_codes"] == ["C-2-1"]


def test_c2_auditor_strict_schema_is_valid():
    from jsonschema import Draft202012Validator

    Draft202012Validator.check_schema(S.C2_AUDITOR_OUTPUT_SCHEMA)
    assert set(S.C2_AUDITOR_OUTPUT_SCHEMA["properties"]) == set(
        S.C2AuditorOutput.model_fields
    )
