"""LLM client 測試（monkeypatch，無需真 LLM key）：OpenAI SDK 呼叫 + flex tier 回退 + exact-cache。"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from openai import APITimeoutError, BadRequestError, InternalServerError, RateLimitError

from app.judge.llm import client

_REQ = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


def _sdk_status_err(cls, status: int, *, code=None, param=None, message="err"):
    """建 OpenAI SDK APIStatusError 子類（RateLimitError/BadRequestError/InternalServerError）；
    帶 body 使 e.code/e.param 可讀（比照真實 API 回應，供 client._complete 的 typed 分流測試）。"""
    body: dict = {}
    if code is not None:
        body["code"] = code
    if param is not None:
        body["param"] = param
    return cls(message, response=httpx.Response(status, request=_REQ), body=body or None)


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
            raise _sdk_status_err(RateLimitError, 429, code="resource_unavailable")
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
    """讀取閘關（顯式重新初判）→ 照打 API；但寫入恆開 → 重開讀取後命中。"""
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


# ── 錯誤分類（typed exceptions）：timeout/429/5xx 快速失敗、非 OpenAI 才降級 ──
def _raiser(exc, calls):
    """回一個每次呼叫都記錄 kwargs 並拋 exc 的 _complete 替身。"""

    def _f(cfg, kwargs, ck):
        calls.append(dict(kwargs))
        raise exc

    return _f


def test_chat_json_timeout_raises_without_futile_retry(monkeypatch) -> None:
    """timeout（APITimeoutError）→ 如實拋、不再誤判為 json_schema 做無用 json_object 重試（只呼叫一次）。"""
    calls: list = []
    monkeypatch.setattr(client, "_complete", _raiser(APITimeoutError(request=_REQ), calls))
    monkeypatch.setattr(client, "_resolve", lambda: _cfg(model="gpt-5-mini"))
    monkeypatch.setattr(client, "_record_usage", lambda *a, **k: None)
    with pytest.raises(APITimeoutError):
        client.chat_json("s", "u", "attribute", schema={"type": "object"})
    assert len(calls) == 1  # 帶 schema 也不做降級重試（修 bug 前會是 2、逾時翻倍）


def test_chat_json_generic_rate_limit_raises(monkeypatch) -> None:
    """一般 429（非 flex resource_unavailable）→ SDK 已重試耗盡，如實拋、不降級（只呼叫一次）。"""
    calls: list = []
    monkeypatch.setattr(client, "_complete", _raiser(_sdk_status_err(RateLimitError, 429), calls))
    monkeypatch.setattr(client, "_resolve", lambda: _cfg(model="gpt-5-mini"))
    monkeypatch.setattr(client, "_record_usage", lambda *a, **k: None)
    with pytest.raises(RateLimitError):
        client.chat_json("s", "u", "attribute", schema={"type": "object"})
    assert len(calls) == 1


def test_chat_json_500_raises(monkeypatch) -> None:
    """5xx（InternalServerError）→ SDK 已重試耗盡，如實拋（只呼叫一次）。"""
    calls: list = []
    monkeypatch.setattr(
        client, "_complete", _raiser(_sdk_status_err(InternalServerError, 500), calls)
    )
    monkeypatch.setattr(client, "_resolve", lambda: _cfg(model="gpt-5-mini"))
    monkeypatch.setattr(client, "_record_usage", lambda *a, **k: None)
    with pytest.raises(InternalServerError):
        client.chat_json("s", "u", "attribute", schema={"type": "object"})
    assert len(calls) == 1


def test_chat_json_openai_400_raises_no_downgrade(monkeypatch) -> None:
    """OpenAI（含 gpt-5）的 400 一律如實拋，不做 json_object 降級（即使帶 schema、訊息含 json_schema）。"""
    calls: list = []
    err = _sdk_status_err(BadRequestError, 400, message="invalid json_schema")
    monkeypatch.setattr(client, "_complete", _raiser(err, calls))
    monkeypatch.setattr(
        client, "_resolve", lambda: _cfg(model="gpt-5-mini")
    )  # 空 base_url = openai
    monkeypatch.setattr(client, "_record_usage", lambda *a, **k: None)
    with pytest.raises(BadRequestError):
        client.chat_json("s", "u", "attribute", schema={"type": "object"})
    assert len(calls) == 1


def test_chat_json_non_openai_json_schema_falls_back(monkeypatch) -> None:
    """非 OpenAI 端點回 400 且訊息指涉 json_schema → 回退 json_object 重試（第二打改 response_format）。"""
    calls: list[dict] = []

    def _boom_then_ok(cfg, kwargs, ck):
        calls.append(dict(kwargs))
        if kwargs.get("response_format", {}).get("type") == "json_schema":
            raise _sdk_status_err(BadRequestError, 400, message="json_schema not supported")
        return _fake_resp()

    monkeypatch.setattr(client, "_complete", _boom_then_ok)
    monkeypatch.setattr(
        client,
        "_resolve",
        lambda: _cfg(base_url="https://generativelanguage.googleapis.com/v1beta"),
    )
    monkeypatch.setattr(client, "_record_usage", lambda *a, **k: None)
    out = client.chat_json("s", "u", "attribute", schema={"type": "object"})
    assert out == {"a": 1}
    assert len(calls) == 2 and calls[1]["response_format"] == {"type": "json_object"}


def test_chat_json_non_openai_response_format_unsupported(monkeypatch) -> None:
    """非 OpenAI 端點完全不支援 response_format → 去除該參數重試（第二打不帶 response_format）。"""
    calls: list[dict] = []

    def _boom_then_ok(cfg, kwargs, ck):
        calls.append(dict(kwargs))
        if "response_format" in kwargs:
            raise _sdk_status_err(BadRequestError, 400, message="response_format is not supported")
        return _fake_resp()

    monkeypatch.setattr(client, "_complete", _boom_then_ok)
    monkeypatch.setattr(
        client,
        "_resolve",
        lambda: _cfg(base_url="https://generativelanguage.googleapis.com/v1beta"),
    )
    monkeypatch.setattr(client, "_record_usage", lambda *a, **k: None)
    out = client.chat_json("s", "u", "attribute", schema={"type": "object"})
    assert out == {"a": 1}
    assert len(calls) == 2 and "response_format" not in calls[1]


# ── thinking / reasoning_effort per-provider 組參數 + 錯誤驅動降級 ──
_ARK = "https://ark.ap-southeast.bytepluses.com/api/v3"


def test_reasoning_kwargs_bytedance_off_native_switch_without_effort() -> None:
    """ByteDance thinking=off → 原生 extra_body 開關；不併送 reasoning_effort（Ark 400 Invalid combination）。"""
    out = client._reasoning_kwargs(
        {"base_url": _ARK, "thinking": "off", "reasoning_effort": "medium"}
    )
    assert out == {"extra_body": {"thinking": {"type": "disabled"}}}


def test_reasoning_kwargs_bytedance_on_sends_switch_and_effort() -> None:
    """ByteDance thinking=on → 原生開關 + reasoning_effort 並送（Ark 支援並用、effort 調深度）。"""
    out = client._reasoning_kwargs({"base_url": _ARK, "thinking": "on", "reasoning_effort": "high"})
    assert out == {"extra_body": {"thinking": {"type": "enabled"}}, "reasoning_effort": "high"}


def test_reasoning_kwargs_openai_off_maps_effort_none() -> None:
    """OpenAI / Gemini 無獨立 thinking 參數：off ≙ reasoning_effort=none（不支援者由降級層轉 minimal）。"""
    out = client._reasoning_kwargs(
        {"base_url": "", "thinking": "off", "reasoning_effort": "medium"}
    )
    assert out == {"reasoning_effort": "none"}


def test_reasoning_kwargs_default_passthrough() -> None:
    """thinking=default（或缺省）→ 不干涉，僅傳 reasoning_effort（既有行為）；effort=default/缺省不送。"""
    out = client._reasoning_kwargs(
        {"base_url": "", "thinking": "default", "reasoning_effort": "medium"}
    )
    assert out == {"reasoning_effort": "medium"}
    assert client._reasoning_kwargs({"base_url": ""}) == {}


def test_chat_json_degrades_unsupported_reasoning_effort(monkeypatch) -> None:
    """400 點名 reasoning_effort（如 gpt-5-mini 不吃 xhigh）→ 就地降級重試（xhigh→high），不如實拋。"""
    calls: list[dict] = []

    def _boom_then_ok(cfg, kwargs, ck):
        calls.append(dict(kwargs))
        if kwargs.get("reasoning_effort") == "xhigh":
            raise _sdk_status_err(
                BadRequestError,
                400,
                message="Unsupported value: 'reasoning_effort' does not support 'xhigh' with this model.",
            )
        return _fake_resp()

    monkeypatch.setattr(client, "_complete", _boom_then_ok)
    monkeypatch.setattr(
        client, "_resolve", lambda: {**_cfg(model="gpt-5-mini"), "reasoning_effort": "xhigh"}
    )
    monkeypatch.setattr(client, "_record_usage", lambda *a, **k: None)
    assert client.chat_json("s", "u") == {"a": 1}
    assert len(calls) == 2 and calls[1]["reasoning_effort"] == "high"
