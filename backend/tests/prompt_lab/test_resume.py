"""Resume + 429/5xx 退避 + Schema error≠棄權 + dry-run 零 API（PRD §10.4/§19）。"""

from __future__ import annotations

import common
import evaluate_prompt as ep
import prompt_parser as pp
from fake_client import FakeResponsesClient
from openai_gateway import Gateway, RetryableError

_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["attributions"],
    "properties": {"attributions": {"type": "array"}},
}


def _gw(responder, **kw):
    return Gateway(client=FakeResponsesClient(responder), sleep=lambda s: None, **kw)


def test_retry_recovers_after_transient_errors():
    calls = {"n": 0}

    def flaky(*a):
        calls["n"] += 1
        return RetryableError("429") if calls["n"] < 3 else {"attributions": []}

    r = _gw(flaky).structured(system="s", user="u", json_schema=_SCHEMA, schema_name="t", model="m")
    assert r.ok and r.attempts == 3


def test_retry_exhausted_is_api_error():
    r = _gw(lambda *a: RetryableError("503"), max_retries=5).structured(
        system="s", user="u", json_schema=_SCHEMA, schema_name="t", model="m"
    )
    assert r.status == "api_error" and r.attempts == 5 and not r.ok


def test_non_retryable_not_retried():
    r = _gw(lambda *a: ValueError("400 bad")).structured(
        system="s", user="u", json_schema=_SCHEMA, schema_name="t", model="m"
    )
    assert r.status == "api_error" and r.attempts == 1


def _case(cid, text="頁面沒說明時長問題"):
    return {
        "case_id": cid,
        "domain_under_test": "C-1",
        "layer": 1,
        "text": text,
        "input_polarity": "negative",
        "expected_domain": "true",
        "expected_l2_codes": ["C-1-2"],
        "forbidden_l2_codes": [],
        "expected_evidence_quotes": ["沒說明時長"],
        "case_family": "rule_unit",
        "expression_variant": "direct",
        "difficulty": "easy",
        "language": "zh-tw",
        "boundary_with": None,
        "contrast_pair_id": None,
        "contrast_key": None,
        "origin": "ai_generated",
        "label_supported": True,
        "evidence_quotes_valid": True,
        "dataset_version": "t",
        "split": "dev",
        "human_reviewed": True,
    }


def test_schema_error_is_not_abstain(c1_prompt_path):
    """Schema 失敗必須記為錯誤（pred_hit=None），NEVER 當作空歸因棄權。"""
    parsed = pp.parse_prompt_file(c1_prompt_path)
    # 非 JSON 輸出 → parse_error
    bad = _gw(lambda *a: "這不是 JSON {")
    r = ep.judge_once(bad, parsed, _case("c1"), 0, "m", "run")
    assert (
        r.error == "schema_invalid" and r.predicted_domain_hit is None and r.schema_valid is False
    )
    # 真正的棄權：合法空歸因 → schema_valid，pred_hit=False，error=None
    good = _gw(lambda *a: {"attributions": []})
    r2 = ep.judge_once(good, parsed, _case("c2"), 0, "m", "run")
    assert r2.schema_valid and r2.predicted_domain_hit is False and r2.error is None


def test_refusal_and_incomplete_distinct(c1_prompt_path):
    parsed = pp.parse_prompt_file(c1_prompt_path)
    assert (
        ep.judge_once(_gw(lambda *a: ("refusal", "拒答")), parsed, _case("c"), 0, "m", "run").error
        == "refusal"
    )
    assert (
        ep.judge_once(
            _gw(lambda *a: ("incomplete", "半截")), parsed, _case("c"), 0, "m", "run"
        ).error
        == "incomplete"
    )


def test_evaluate_resume_skips_successful(tmp_path, c1_prompt_path):
    ds = tmp_path / "dev.jsonl"
    common.write_jsonl(ds, [_case("c1"), _case("c2", "頁面沒寫門票費用")])
    out = tmp_path / "run"
    gw = _gw(
        lambda *a: {
            "attributions": [
                {
                    "l2_code": "C-1-2",
                    "confidence": 0.9,
                    "summary": [{"lang": "zh-tw", "text": "x"}],
                    "evidence_quote": "沒說明時長",
                }
            ]
        }
    )
    args = [
        "--prompt",
        str(c1_prompt_path),
        "--dataset",
        str(ds),
        "--out",
        str(out),
        "--model",
        "m",
        "--repeats",
        "2",
        "--all",
        "--confirm-cost",
    ]
    assert ep.main(args, gateway=gw) == 0
    n1 = len(common.read_jsonl(out / "raw_results.jsonl"))
    assert n1 == 4  # 2 cases × 2 repeats
    # resume：全部成功 → 不重跑，行數不變
    assert ep.main(args + ["--resume"], gateway=gw) == 0
    assert len(common.read_jsonl(out / "raw_results.jsonl")) == n1


def test_dry_run_zero_api(tmp_path, c1_prompt_path):
    ds = tmp_path / "dev.jsonl"
    common.write_jsonl(ds, [_case("c1")])
    out = tmp_path / "run"

    def boom(*a):
        raise AssertionError("dry-run 不得呼叫 API")

    rc = ep.main(
        [
            "--prompt",
            str(c1_prompt_path),
            "--dataset",
            str(ds),
            "--out",
            str(out),
            "--model",
            "m",
            "--repeats",
            "3",
            "--dry-run",
        ],
        gateway=_gw(boom),
    )
    assert rc == 0
    assert not (out / "raw_results.jsonl").exists()
