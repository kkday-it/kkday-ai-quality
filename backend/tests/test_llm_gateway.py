"""LLM gateway 分派 + LiteLLM 參數映射測試（monkeypatch，無需真 LLM key）。

驗證 drop-in 正確性：預設走 OpenAI SDK（prompt_cache_key 頂層）；LLM_GATEWAY=litellm 時走 litellm
（openai/ 前綴 + api_base + api_key + drop_params + prompt_cache_key 走 extra_body），且回應同構 →
chat_json 端到端解析 JSON + usage sink 抽 cached_tokens 皆與既有一致。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.judge.llm import client


def _fake_resp(content: str = '{"a": 1}', prompt: int = 10, completion: int = 5, cached: int = 0):
    """OpenAI/litellm 同構回應：.choices[0].message.content + .usage.*（含 prompt_tokens_details）。"""
    usage = SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
        prompt_tokens_details=SimpleNamespace(cached_tokens=cached),
    )
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=usage)


def _cfg(base_url: str = "", model: str = "gpt-5-nano"):
    return {"token": "sk-x", "base_url": base_url, "model": model, "temperature": None, "reasoning_effort": "default"}


def test_complete_litellm_maps_custom_endpoint(monkeypatch) -> None:
    """自訂 base_url → openai/ 前綴 + api_base + api_key + drop_params；prompt_cache_key 走 extra_body。"""
    import litellm

    cap: dict = {}
    monkeypatch.setattr(litellm, "completion", lambda **kw: cap.update(kw) or _fake_resp())
    kwargs = {"model": "gpt-5-nano", "messages": [{"role": "user", "content": "hi"}], "response_format": {"type": "json_object"}}
    client._complete_litellm(_cfg(base_url="https://ep.example/v1"), dict(kwargs), "ck-123")
    assert cap["model"] == "openai/gpt-5-nano"
    assert cap["api_base"] == "https://ep.example/v1"
    assert cap["api_key"] == "sk-x"
    assert cap["drop_params"] is True
    assert cap["extra_body"] == {"prompt_cache_key": "ck-123"}
    assert cap["response_format"] == {"type": "json_object"}


def test_complete_litellm_no_base_url_no_prefix(monkeypatch) -> None:
    """無 base_url（OpenAI 官方）→ 不加 openai/ 前綴、不帶 api_base；無 cache_key → 不帶 extra_body。"""
    import litellm

    cap: dict = {}
    monkeypatch.setattr(litellm, "completion", lambda **kw: cap.update(kw) or _fake_resp())
    client._complete_litellm(_cfg(base_url=""), {"model": "gpt-5-nano", "messages": []}, None)
    assert cap["model"] == "gpt-5-nano"
    assert "api_base" not in cap
    assert "extra_body" not in cap


def test_complete_openai_toplevel_cache_key(monkeypatch) -> None:
    """OpenAI 路徑：prompt_cache_key 放頂層 kwarg（非 extra_body）。"""
    cap: dict = {}
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: cap.update(kw) or _fake_resp()))
    )
    monkeypatch.setattr(client, "_get_client", lambda t, b: fake_client)
    client._complete_openai(_cfg(), {"model": "m", "messages": []}, "ck")
    assert cap["prompt_cache_key"] == "ck"


def test_complete_dispatches_by_gateway(monkeypatch) -> None:
    """_complete 依 env.llm_gateway 分派：litellm → litellm.completion；否則 OpenAI SDK。"""
    import litellm

    litellm_hit, openai_hit = [], []
    monkeypatch.setattr(litellm, "completion", lambda **kw: litellm_hit.append(1) or _fake_resp())
    monkeypatch.setattr(
        client,
        "_get_client",
        lambda t, b: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: openai_hit.append(1) or _fake_resp()))
        ),
    )
    kwargs = {"model": "m", "messages": []}
    monkeypatch.setattr(client.env, "llm_gateway", "litellm")
    client._complete(_cfg(), dict(kwargs), None)
    monkeypatch.setattr(client.env, "llm_gateway", "openai")
    client._complete(_cfg(), dict(kwargs), None)
    assert litellm_hit == [1] and openai_hit == [1]


def test_chat_json_via_litellm_parses_and_reports_usage(monkeypatch) -> None:
    """端到端：litellm gateway → chat_json 正確解析 JSON + usage sink 收到 cached_tokens。"""
    import litellm

    monkeypatch.setattr(litellm, "completion", lambda **kw: _fake_resp('{"x": 7}', cached=3))
    monkeypatch.setattr(client.env, "llm_gateway", "litellm")
    seen: list = []
    client.set_usage_sink(lambda m, p, c, ca: seen.append((m, p, c, ca)))
    try:
        out = client.chat_json("system", "user")
    finally:
        client.set_usage_sink(None)
    assert out == {"x": 7}
    assert seen and seen[0][3] == 3  # cached_tokens 透傳
