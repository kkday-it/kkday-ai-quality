"""LLM client 測試（monkeypatch，無需真 LLM key）：OpenAI SDK 呼叫 + flex tier 回退 + exact-cache。"""

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


def test_complete_openai_toplevel_cache_key(monkeypatch) -> None:
    """OpenAI 路徑：prompt_cache_key 放頂層 kwarg。"""
    cap: dict = {}
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kw: cap.update(kw) or _fake_resp())
        )
    )
    monkeypatch.setattr(client, "_get_client", lambda t, b: fake_client)
    client._complete(_cfg(), {"model": "m", "messages": []}, "ck")
    assert cap["prompt_cache_key"] == "ck"


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
