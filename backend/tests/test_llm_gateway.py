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
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=usage
    )


def _cfg(base_url: str = "", model: str = "gpt-5-nano"):
    return {
        "token": "sk-x",
        "base_url": base_url,
        "model": model,
        "temperature": None,
        "reasoning_effort": "default",
    }


def test_complete_litellm_maps_custom_endpoint(monkeypatch) -> None:
    """自訂 base_url → openai/ 前綴 + api_base + api_key + drop_params；prompt_cache_key 走 extra_body。"""
    import litellm

    cap: dict = {}
    monkeypatch.setattr(litellm, "completion", lambda **kw: cap.update(kw) or _fake_resp())
    kwargs = {
        "model": "gpt-5-nano",
        "messages": [{"role": "user", "content": "hi"}],
        "response_format": {"type": "json_object"},
    }
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
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kw: cap.update(kw) or _fake_resp())
        )
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
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **kw: openai_hit.append(1) or _fake_resp()
                )
            )
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


def test_chat_json_flex_tier_injection(monkeypatch) -> None:
    """service_tier=flex（OpenAI provider）→ 請求帶 service_tier；計價以 flex 半價入 llm_usage 列。"""
    cap: dict = {}
    monkeypatch.setattr(
        client, "_complete", lambda cfg, kwargs, ck: cap.update(kwargs) or _fake_resp()
    )
    monkeypatch.setattr(
        client, "_resolve", lambda: {**_cfg(model="gpt-5-mini"), "service_tier": "flex"}
    )
    rows: list = []
    monkeypatch.setattr(client, "_record_usage", lambda *a, **k: rows.append(a))
    client.chat_json("s", "u", "polarity")
    assert cap["service_tier"] == "flex"
    # _record_usage 收到的 cfg 仍帶 flex（無 429 → 實際生效 tier 不變）
    assert rows and rows[0][1].get("service_tier") == "flex"


def test_chat_json_flex_resource_unavailable_falls_back_standard(monkeypatch) -> None:
    """flex 429 resource_unavailable → 去掉 service_tier 回退標準重打；計價 cfg 改標準。"""
    calls: list[dict] = []

    def _boom_then_ok(cfg, kwargs, ck):
        calls.append(dict(kwargs))
        if kwargs.get("service_tier") == "flex":
            raise RuntimeError(
                "Error code: 429 - resource_unavailable: The flex tier is at capacity"
            )
        return _fake_resp()

    monkeypatch.setattr(client, "_complete", _boom_then_ok)
    monkeypatch.setattr(
        client, "_resolve", lambda: {**_cfg(model="gpt-5-mini"), "service_tier": "flex"}
    )
    rows: list = []
    monkeypatch.setattr(client, "_record_usage", lambda *a, **k: rows.append(a))
    out = client.chat_json("s", "u", "polarity")
    assert out == {"a": 1}
    assert len(calls) == 2 and "service_tier" not in calls[1]  # 第二打不帶 flex
    assert rows and rows[0][1].get("service_tier") is None  # 計價按實際生效（標準）


def test_chat_json_non_openai_provider_drops_tier(monkeypatch) -> None:
    """非 OpenAI provider（自訂 base_url）→ 不送 service_tier（避免 400）。"""
    cap: dict = {}
    monkeypatch.setattr(
        client, "_complete", lambda cfg, kwargs, ck: cap.update(kwargs) or _fake_resp()
    )
    monkeypatch.setattr(
        client,
        "_resolve",
        lambda: {
            **_cfg(base_url="https://generativelanguage.googleapis.com/v1beta"),
            "service_tier": "flex",
        },
    )
    monkeypatch.setattr(client, "_record_usage", lambda *a, **k: None)
    client.chat_json("s", "u")
    assert "service_tier" not in cap


def _tmp_cache(monkeypatch, tmp_path):
    """快取測試 fixture 配件：開啟 exact-cache 並隔離到 tmp 目錄（不碰真實 data/llm_cache）。"""
    import diskcache

    monkeypatch.setattr(client.env, "llm_exact_cache", True)
    monkeypatch.setattr(client, "_exact_cache", diskcache.Cache(str(tmp_path)))


def test_exact_cache_hit_skips_api_call(monkeypatch, tmp_path) -> None:
    """同 prompt+model 第二次呼叫 → 命中快取零 API 呼叫；不同 prompt → miss 重打。"""
    _tmp_cache(monkeypatch, tmp_path)
    calls: list = []
    monkeypatch.setattr(
        client, "_complete", lambda cfg, kwargs, ck: calls.append(1) or _fake_resp('{"x": 1}')
    )
    monkeypatch.setattr(client, "_resolve", lambda: _cfg())
    monkeypatch.setattr(client, "_record_usage", lambda *a, **k: None)
    assert client.chat_json("sys", "同一則評論") == {"x": 1}
    assert client.chat_json("sys", "同一則評論") == {"x": 1}
    assert len(calls) == 1  # 第二次命中快取，零 API 呼叫
    client.chat_json("sys", "另一則評論")
    assert len(calls) == 2  # 內容不同 → miss


def test_exact_cache_read_gate_write_always(monkeypatch, tmp_path) -> None:
    """讀取閘關（顯式重判）→ 照打 API；但寫入恆開 → 重開讀取後命中。"""
    _tmp_cache(monkeypatch, tmp_path)
    calls: list = []
    monkeypatch.setattr(
        client, "_complete", lambda cfg, kwargs, ck: calls.append(1) or _fake_resp()
    )
    monkeypatch.setattr(client, "_resolve", lambda: _cfg())
    monkeypatch.setattr(client, "_record_usage", lambda *a, **k: None)
    try:
        client.set_llm_cache_read(False)
        client.chat_json("sys", "評論A")
        client.chat_json("sys", "評論A")
        assert len(calls) == 2  # 讀取閘關 → 不重用（使用者要求真的重打）
        client.set_llm_cache_read(True)
        client.chat_json("sys", "評論A")
        assert len(calls) == 2  # 寫入恆開 → 先前結果已回填，重開讀取即命中
    finally:
        client.set_llm_cache_read(True)


def test_exact_cache_key_ignores_service_tier(monkeypatch, tmp_path) -> None:
    """service_tier 不入 key（flex/標準語義同結果）：flex 打過一次，標準 tier 再問直接命中。"""
    _tmp_cache(monkeypatch, tmp_path)
    calls: list = []
    monkeypatch.setattr(
        client, "_complete", lambda cfg, kwargs, ck: calls.append(1) or _fake_resp()
    )
    monkeypatch.setattr(client, "_record_usage", lambda *a, **k: None)
    monkeypatch.setattr(client, "_resolve", lambda: {**_cfg(), "service_tier": "flex"})
    client.chat_json("sys", "評論B")
    monkeypatch.setattr(client, "_resolve", lambda: _cfg())  # 換回標準 tier
    client.chat_json("sys", "評論B")
    assert len(calls) == 1
