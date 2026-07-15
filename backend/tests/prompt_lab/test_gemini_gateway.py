"""Gemini Generator gateway：路由、结构化输出与失败分类。"""

from __future__ import annotations

import json
from types import SimpleNamespace

from gemini_gateway import GeminiGateway, provider_for_model
from openai_gateway import RetryableError


class _Completions:
    def __init__(self, responder):
        self.responder = responder
        self.payloads: list[dict] = []

    def create(self, **payload):
        self.payloads.append(payload)
        result = self.responder(payload)
        if isinstance(result, Exception):
            raise result
        if isinstance(result, dict):
            result = json.dumps(result, ensure_ascii=False)
        return SimpleNamespace(
            id="gemini-request-1",
            usage=SimpleNamespace(prompt_tokens=12, completion_tokens=8),
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content=result, refusal=None),
                )
            ],
        )


class _Client:
    def __init__(self, responder):
        self.completions = _Completions(responder)
        self.chat = SimpleNamespace(completions=self.completions)


_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["cases"],
    "properties": {"cases": {"type": "array"}},
}


def test_provider_auto_routes_gemini_model():
    assert provider_for_model("gemini-3.5-flash") == "gemini"
    assert provider_for_model("gpt-5.5-2026-04-23") == "openai"
    assert provider_for_model("custom", "gemini") == "gemini"


def test_structured_uses_chat_json_schema_and_records_usage():
    client = _Client(lambda _: {"cases": []})
    gateway = GeminiGateway(client=client, sleep=lambda _: None, reasoning_effort="medium")
    result = gateway.structured(
        system="system",
        user="user",
        json_schema=_SCHEMA,
        schema_name="c2_generator_output",
        model="gemini-3.5-flash",
        meta={"cell_id": "c2-test"},
    )

    assert result.ok
    assert result.parsed == {"cases": []}
    assert result.input_tokens == 12 and result.output_tokens == 8
    payload = client.completions.payloads[0]
    assert payload["model"] == "gemini-3.5-flash"
    assert payload["messages"][0] == {"role": "system", "content": "system"}
    assert payload["response_format"]["json_schema"]["schema"] == _SCHEMA
    assert payload["reasoning_effort"] == "medium"


def test_retryable_error_recovers():
    calls = {"n": 0}

    def responder(_):
        calls["n"] += 1
        return RetryableError("429") if calls["n"] == 1 else {"cases": []}

    result = GeminiGateway(client=_Client(responder), sleep=lambda _: None).structured(
        system="s",
        user="u",
        json_schema=_SCHEMA,
        schema_name="test",
        model="gemini-3.5-flash",
    )
    assert result.ok and result.attempts == 2


def test_non_json_is_parse_error():
    result = GeminiGateway(client=_Client(lambda _: "not-json"), sleep=lambda _: None).structured(
        system="s",
        user="u",
        json_schema=_SCHEMA,
        schema_name="test",
        model="gemini-3.5-flash",
    )
    assert result.status == "parse_error" and not result.ok
