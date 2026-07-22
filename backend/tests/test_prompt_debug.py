"""售後根因 Prompt 調試台：分類 SSOT、交叉欄位契約與單次配置覆蓋。"""

from __future__ import annotations

import json
from types import SimpleNamespace

from app.judge import prompt_debug


def _base_result(**overrides):
    value = {
        "category": "憑證/取票資訊未送達或不知如何使用",
        "theme": "[104]訂單確認問題",
        "likely_cause": "憑證送達延遲",
        "modify_target": None,
        "oot_subtype": None,
        "summary": "旅客出發前仍未收到電子票，要求協助確認送達時程。",
        "sentiment": "negative",
        "money_mention_flag": False,
        "fulfillment_mention_flag": True,
        "urgency_flag": True,
        "multi_issue_flag": False,
        "confidence": 0.93,
        "tail_theme": None,
    }
    value.update(overrides)
    return value


def test_defaults_are_derived_from_taxonomy() -> None:
    payload = prompt_debug.defaults_payload()
    schema = payload["output_schema"]
    assert payload["category_count"] == 25
    assert payload["theme_count"] == 5
    assert "__OUT_OF_TAXONOMY__" in schema["properties"]["category"]["enum"]
    assert [field["key"] for field in payload["output_fields"]] == [
        "theme",
        "category",
        "likely_cause",
        "modify_target",
        "summary",
        "sentiment",
        "money_mention_flag",
        "fulfillment_mention_flag",
        "urgency_flag",
        "multi_issue_flag",
        "confidence",
        "tail_theme",
    ]
    assert "oot_subtype" not in {field["key"] for field in payload["output_fields"]}
    assert payload["sources"]["field_definitions_document"]["document_id"] == (
        "1FFFqsGPUhOd0oVG4uDbSgVfsdqdYYRuy5fLIE0tYpMA"
    )
    assert "$schema" not in schema
    assert "{{TAXONOMY_JSON}}" not in payload["system_prompt"]


def test_slashes_inside_controlled_causes_are_not_split() -> None:
    taxonomy = prompt_debug.load_taxonomy()
    causes = {cause for category in taxonomy["categories"] for cause in category["likely_causes"]}
    assert "下單流程統編/抬頭欄位易漏填或誤填" in causes
    assert "代收轉付收據性質未於下單/商品頁說明" in causes
    assert "用戶對發票/收據/三聯式概念混淆" in causes
    assert "商品頁說明" not in causes


def test_validate_result_accepts_controlled_non_oot() -> None:
    assert prompt_debug.validate_result(_base_result()) == []


def test_validate_result_enforces_summary_length_from_field_definition() -> None:
    issues = prompt_debug.validate_result(_base_result(summary="太短"))
    assert issues and issues[0].startswith("Schema summary:")


def test_validate_result_rejects_cross_category_cause_and_theme() -> None:
    issues = prompt_debug.validate_result(
        _base_result(theme="[101]訂單取消", likely_cause="退款作業時程長")
    )
    assert "theme 必須是 [104]訂單確認問題" in issues
    assert "likely_cause 不屬於該 category 的受控選項" in issues


def test_validate_result_accepts_oot_contract() -> None:
    value = _base_result(
        category="__OUT_OF_TAXONOMY__",
        theme="OOT跳出",
        likely_cause=None,
        oot_subtype="售前_商品資訊詢問",
        tail_theme="親子適用條件詢問",
    )
    assert prompt_debug.validate_result(value) == []


def test_validate_result_requires_modify_target_for_93() -> None:
    value = _base_result(
        category="修改受限（商品規則/供應商政策不允許改）",
        theme="[93]訂單申請修改",
        likely_cause="商品規則不允許改",
    )
    assert "[93] category 必須填 modify_target" in prompt_debug.validate_result(value)
    value["modify_target"] = "改日期/時段/班次"
    assert prompt_debug.validate_result(value) == []


def test_build_effective_config_can_clear_temperature_without_clearing_secrets() -> None:
    base = {
        "token": "secret",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-5-mini",
        "temperature": 0.7,
        "thinking": "on",
    }
    result = prompt_debug.build_effective_config(
        base,
        {"model": "gpt-5.4-mini", "temperature": None, "thinking": None},
    )
    assert result["token"] == "secret"
    assert result["model"] == "gpt-5.4-mini"
    assert result["temperature"] is None
    assert result["thinking"] == "on"


def test_stream_frames_uses_final_chunk_usage_for_same_call(monkeypatch) -> None:
    raw = json.dumps(_base_result(), ensure_ascii=False)
    usage = SimpleNamespace(
        prompt_tokens=1_000,
        completion_tokens=200,
        prompt_tokens_details=SimpleNamespace(cached_tokens=100),
        completion_tokens_details=SimpleNamespace(reasoning_tokens=40),
    )
    chunks = iter(
        [
            SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content=raw[:40]))], usage=None
            ),
            SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content=raw[40:]))], usage=None
            ),
            SimpleNamespace(choices=[], usage=usage),
        ]
    )
    monkeypatch.setattr(prompt_debug.app_settings, "resolve_provider_token", lambda _: "sk-test")
    monkeypatch.setattr(prompt_debug, "_create_stream", lambda cfg, kwargs: (chunks, []))
    recorded: list[dict] = []
    monkeypatch.setattr(
        prompt_debug,
        "_record_usage_best_effort",
        lambda cfg, payload, job_id: recorded.append(payload),
    )

    frames = list(
        prompt_debug.stream_frames(
            "[USER] 尚未收到電子票",
            "只輸出 JSON",
            {
                "token": "sk-test",
                "base_url": "",
                "model": "gpt-5-mini",
                "temperature": None,
                "thinking": "off",
                "reasoning_effort": "minimal",
            },
        )
    )

    assert sum(frame.startswith("event: delta") for frame in frames) == 2
    result_frame = next(frame for frame in frames if frame.startswith("event: result"))
    assert '"valid": true' in result_frame
    usage_frame = next(frame for frame in frames if frame.startswith("event: usage"))
    assert '"prompt_tokens": 1000' in usage_frame
    assert '"completion_tokens": 200' in usage_frame
    assert '"reasoning_tokens": 40' in usage_frame
    assert recorded and recorded[0]["total_tokens"] == 1_200
    assert recorded[0]["cost_usd"] > 0
