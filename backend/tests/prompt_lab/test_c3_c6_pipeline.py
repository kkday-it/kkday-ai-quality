"""C3～C6 配置、计划、Prompt 路由与 fake client（零 API）。"""

from __future__ import annotations

import json

import audit_cases as audit
import generate_cases as gen
import prompt_parser as pp
import pytest
import schemas as S
from domains import load_domain
from fake_client import FakeResponsesClient
from openai_gateway import Gateway

DOMAINS = ["C-3", "C-4", "C-5", "C-6"]
TARGETS = {"C-3": (130, 246, 36), "C-4": (90, 108, 18), "C-5": (90, 108, 18), "C-6": (120, 198, 18)}


def _gw(responder):
    return Gateway(client=FakeResponsesClient(responder), sleep=lambda _: None)


@pytest.mark.parametrize("domain", DOMAINS)
def test_domain_config_plan_and_judge_schema(domain, plans_dir, prompts_dir):
    cfg = load_domain(domain)
    slug = domain.lower().replace("-", "")
    l1 = S.Plan(**json.loads((plans_dir / f"{slug}_layer1_plan.json").read_text(encoding="utf-8")))
    l2 = S.Plan(**json.loads((plans_dir / f"{slug}_layer2_plan.json").read_text(encoding="utf-8")))
    expected_l1, expected_l2, expected_pair = TARGETS[domain]
    assert (l1.total_target, l2.total_target) == (expected_l1, expected_l2)
    assert sum(c.target_count for c in l2.cells if c.case_family == "l2_pair") == expected_pair
    judge_file = {"C-3":"03_C-3_supplier.md", "C-4":"04_C-4_platform.md", "C-5":"05_C-5_service.md", "C-6":"06_C-6_customer.md"}[domain]
    parsed = pp.parse_prompt_file(prompts_dir / "judges" / judge_file)
    codes = tuple(x["code"] for x in cfg["l2"])
    assert tuple(parsed.schema_l2_enum()) == codes == S.DOMAIN_L2_CODES[domain]
    assert gen.generator_prompt_path(domain).exists()
    assert audit.auditor_prompt_path(domain).exists()


@pytest.mark.parametrize("domain", DOMAINS)
def test_fake_generator_and_auditor_route(domain, plans_dir):
    slug = domain.lower().replace("-", "")
    plan = S.Plan(**json.loads((plans_dir / f"{slug}_layer1_plan.json").read_text(encoding="utf-8")))
    cell = plan.cells[0]
    code = cell.target_l2_codes[0]
    gp = pp.parse_gen_prompt_file(gen.generator_prompt_path(domain))

    def gen_reply(_system, _user, schema_name, _schema, _n):
        assert schema_name == f"{slug}_generator_output"
        return {"cases": [{
            "text": f"这是{domain}的完整责任事实样本{i}，现场细节足以判断。",
            "evidence_quotes": ["现场细节足以判断"], "label_reason": "决定性事实完整。",
            "language": "zh-cn", "pair_side": None,
        } for i in range(cell.target_count)]}

    cases, err = gen.process_cell(_gw(gen_reply), gp, cell, "fake-gemini")
    assert err is None and len(cases) == cell.target_count
    assert all(c.expected_l2_codes == [code] for c in cases)
    ap = pp.parse_gen_prompt_file(audit.auditor_prompt_path(domain))

    def audit_reply(_system, _user, schema_name, schema, _n):
        assert schema_name == f"{slug}_auditor_output"
        assert schema["properties"]["suggested_l2_codes"]["items"]["enum"] == list(S.DOMAIN_L2_CODES[domain])
        return {
            "label_supported": True, "ambiguous": False, "self_contained": True,
            "contains_independent_target_issue": False, "suggested_domain": "true",
            "suggested_l2_codes": [code], "evidence_quotes_valid": True,
            "near_duplicate": False, "pair_minimality_valid": True,
            "review_required": False, "audit_reason": "标签与证据一致。",
        }

    result, err = audit.audit_one(_gw(audit_reply), ap, cases[0], "fake-auditor")
    assert err is None and result["domain_under_test"] == domain
    assert "contains_independent_target_issue" in result


@pytest.mark.parametrize("domain", DOMAINS)
def test_l2_pair_both_true_and_bound(plans_dir, domain):
    slug = domain.lower().replace("-", "")
    plan = S.Plan(**json.loads((plans_dir / f"{slug}_layer2_plan.json").read_text(encoding="utf-8")))
    cell = next(c for c in plan.cells if c.case_family == "l2_pair")
    gp = pp.parse_gen_prompt_file(gen.generator_prompt_path(domain))

    def reply(*_args):
        return {"cases": [
            {"text": "同一场景，A侧决定事实明确。", "evidence_quotes": ["A侧决定事实明确"], "label_reason": "A", "language": "zh-cn", "pair_side": "A"},
            {"text": "同一场景，B侧决定事实明确。", "evidence_quotes": ["B侧决定事实明确"], "label_reason": "B", "language": "zh-cn", "pair_side": "B"},
        ]}

    cases, err = gen.process_cell(_gw(reply), gp, cell, "fake-gemini")
    assert err is None and len(cases) == 2
    assert [c.expected_domain for c in cases] == ["true", "true"]
    assert [c.expected_l2_codes for c in cases] == [[cell.target_l2_codes[0]], [cell.target_l2_codes[1]]]
    assert len({c.contrast_pair_id for c in cases}) == 1
