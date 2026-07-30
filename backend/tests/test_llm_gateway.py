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


def _fake_resp(
    content: str = '{"a": 1}',
    prompt: int = 10,
    completion: int = 5,
    cached: int = 0,
    reasoning: int = 0,
):
    """OpenAI/litellm 同構回應：.choices[0].message.content + .id + .usage.*。

    這是**非串流回應形狀的唯一 SSOT mock**（本檔多數測試共用）。欄位刻意補齊到與真實回應
    等價——`id` 被 `prompt_debug_batch._record_from_response` 讀去落 jsonl 的 request_id、
    `completion_tokens_details` 被 `chat_json` 讀去記 reasoning_tokens；先前缺這兩個，害這兩條
    路徑的形狀變更不會被本檔測試抓到。
    """
    usage = SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
        prompt_tokens_details=SimpleNamespace(cached_tokens=cached),
        completion_tokens_details=SimpleNamespace(reasoning_tokens=reasoning),
    )
    return SimpleNamespace(
        id="chatcmpl-fake",
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=usage,
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


def test_reasoning_kwargs_bytedance_disabled_native_switch_without_effort() -> None:
    """ByteDance thinking=disabled → 原生 extra_body 開關；不併送 reasoning_effort（Ark 400 Invalid combination）。

    2026-07-23 依 Ark 官方 SDK 型別（thinking.type: enabled/disabled/auto）重寫值域，取代舊版 on/off。
    """
    out = client._reasoning_kwargs(
        {"base_url": _ARK, "thinking": "disabled", "reasoning_effort": "medium"}
    )
    assert out == {"extra_body": {"thinking": {"type": "disabled"}}}


def test_reasoning_kwargs_bytedance_enabled_sends_switch_and_effort() -> None:
    """ByteDance thinking=enabled → 原生開關 + reasoning_effort 並送（Ark 支援並用、effort 調深度）。"""
    out = client._reasoning_kwargs(
        {"base_url": _ARK, "thinking": "enabled", "reasoning_effort": "high"}
    )
    assert out == {"extra_body": {"thinking": {"type": "enabled"}}, "reasoning_effort": "high"}


def test_reasoning_kwargs_bytedance_auto_sends_switch_without_effort() -> None:
    """ByteDance thinking=auto → 僅送開關，不併送 reasoning_effort（auto 與 reasoning_effort 的組合行為
    未查到官方或旁證資料，保守起見比照 disabled 不送，避免對生產判決 pipeline 送出未驗證的參數組合；
    見 llm_model.json modelCapabilities gpt-oss-120b-250805 的官方依據）。"""
    out = client._reasoning_kwargs(
        {"base_url": _ARK, "thinking": "auto", "reasoning_effort": "high"}
    )
    assert out == {"extra_body": {"thinking": {"type": "auto"}}}


def test_reasoning_kwargs_openai_gemini_reasoning_effort_passthrough() -> None:
    """OpenAI / Gemini 無獨立 thinking 參數（官方文件逐字確認，見 llm_model.json providers[].docs）：
    reasoning_effort 本身即完整控制面，直接透傳，"none" 是可直接選的正常值（不再靠 thinking=off 模擬）。
    `thinking` 欄位對這兩家不具意義，即使帶了也不影響結果。
    """
    out = client._reasoning_kwargs(
        {"base_url": "", "thinking": "disabled", "reasoning_effort": "none"}
    )
    assert out == {"reasoning_effort": "none"}
    out2 = client._reasoning_kwargs({"base_url": "", "reasoning_effort": "medium"})
    assert out2 == {"reasoning_effort": "medium"}


def test_reasoning_kwargs_default_passthrough() -> None:
    """thinking=default（或缺省）→ 不干涉，僅傳 reasoning_effort（既有行為）；effort=default/缺省不送。"""
    out = client._reasoning_kwargs(
        {"base_url": "", "thinking": "default", "reasoning_effort": "medium"}
    )
    assert out == {"reasoning_effort": "medium"}
    assert client._reasoning_kwargs({"base_url": ""}) == {}
    # bytedance：thinking=default（未customize）→ 不送開關，仍傳 reasoning_effort。
    assert client._reasoning_kwargs(
        {"base_url": _ARK, "thinking": "default", "reasoning_effort": "medium"}
    ) == {"reasoning_effort": "medium"}


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


# ── cache key 形狀守門 ────────────────────────────────────────────────────────────────────
def test_cache_key_golden_hash_guards_kwargs_shape() -> None:
    """`_cache_key` 對固定 kwargs 的 sha256 必須恆定——這是既有 diskcache 命中率的唯一守門員。

    任何人改動 kwargs 的鍵名/鍵序/組裝方式，都會讓全庫既有快取瞬間失效（真金白銀）。此測試
    刻意寫死期望值：紅燈時要問的不是「更新這個 hash」，而是「這次形狀變更是否真的必要、
    以及是否接受全量 miss」。

    `service_tier` 刻意不入 key（僅計價/延遲差異，語義相同），故此處給 flex 也不影響結果。
    """
    kwargs = {
        "model": "gpt-5-mini",
        "messages": [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "attribution", "strict": True, "schema": {"type": "object"}},
        },
        "reasoning_effort": "medium",
        "service_tier": "flex",
    }
    assert (
        client._cache_key(kwargs)
        == "8e10c6900f56aac75cf629f406670103e8e1270f2eafd7668f83eb4fcfce0980"
    )


def test_cache_key_ignores_service_tier_only() -> None:
    """service_tier 不入 key（同語義），其餘任一鍵變動都必須改變 key。"""
    base = {"model": "m", "messages": [], "response_format": {"type": "json_object"}}
    assert client._cache_key(base) == client._cache_key({**base, "service_tier": "flex"})
    assert client._cache_key(base) != client._cache_key({**base, "model": "m2"})
    assert client._cache_key(base) != client._cache_key(
        {**base, "response_format": {"type": "json_schema"}}
    )


# ── 收斂形狀進程記憶（消除判決主線每筆 7 次探測性 400）────────────────────────────────────
@pytest.fixture(autouse=True)
def _clear_shape_memo():
    """記憶是 module 級狀態，測試間必須淨空，否則互相污染。"""
    client._SHAPE_MEMO.clear()
    yield
    client._SHAPE_MEMO.clear()


_ARK = "https://ark.ap-southeast.bytepluses.com/api/v3"


def _degrading_complete(calls, reject_all=True):
    """模擬 Ark 新模型：response_format 一律 400（reject_all）；記錄每次送出的形狀。"""

    def _f(cfg, kwargs, ck, *a, **k):
        calls.append(dict(kwargs))
        if "response_format" in kwargs and reject_all:
            # 逐字採用 2026-07-30 對 seed-2-0-lite-260428 的實測 400 訊息——降級判準是字串比對，
            # mock 訊息不寫實就測不到真正的分支。
            raise _sdk_status_err(
                BadRequestError,
                400,
                param="response_format",
                message=(
                    "The parameter `response_format.type` specified in the request are not "
                    "valid: `json_object` is not supported by this model."
                ),
            )
        return _fake_resp()

    return _f


def test_shape_memo_stops_repeated_probing_400(monkeypatch) -> None:
    """同 (provider, model) 第二次呼叫直接以收斂形狀送出，不再吃探測性 400。

    回歸自 2026-07-30：chat_json 每次重建 kwargs、降級不記憶，判決主線每筆 7 次呼叫
    ＝每筆 7 次無效往返，永遠不收斂。
    """
    calls: list[dict] = []
    monkeypatch.setattr(client, "_complete_effort_safe", _degrading_complete(calls))
    monkeypatch.setattr(client, "_complete", _degrading_complete(calls))
    monkeypatch.setattr(
        client, "_resolve", lambda: _cfg(base_url=_ARK, model="seed-2-0-lite-260428")
    )
    monkeypatch.setattr(client, "_record_usage", lambda *a, **k: None)
    monkeypatch.setattr(client.env, "llm_exact_cache", False)

    client.chat_json("s", "u1")
    first_round = len(calls)
    # 首次走完整階梯：① 帶 response_format 吃 400 → ② 試 Responses 仍 400 → ③ 移除後重打
    assert first_round == 3
    client.chat_json("s", "u2")
    assert len(calls) - first_round == 1  # 第二次：直接以收斂形狀送出
    assert "response_format" not in calls[-1]


def test_shape_memo_untouched_for_providers_that_never_degrade(monkeypatch) -> None:
    """OpenAI/Gemini 從不降級 → 記憶恆空、kwargs 逐位元不變（既有快取命中率零損失）。"""
    calls: list[dict] = []
    monkeypatch.setattr(
        client,
        "_complete_effort_safe",
        lambda cfg, kw, ck, *a, **k: calls.append(dict(kw)) or _fake_resp(),
    )
    monkeypatch.setattr(
        client,
        "_resolve",
        lambda: _cfg(base_url="https://generativelanguage.googleapis.com/v1beta/openai"),
    )
    monkeypatch.setattr(client, "_record_usage", lambda *a, **k: None)
    monkeypatch.setattr(client.env, "llm_exact_cache", False)
    client.chat_json("s", "u", schema={"type": "object"})
    client.chat_json("s", "u", schema={"type": "object"})
    assert not client._SHAPE_MEMO
    assert all(c["response_format"]["type"] == "json_schema" for c in calls)


def test_cache_key_recomputed_after_degradation(monkeypatch, tmp_path) -> None:
    """降級後結果寫在「實際送出形狀」的鍵底下——第二次同輸入應命中快取而非重打。

    未修前：json_object 的產物被存在 json_schema 的鍵上，下次命中一個「標示 strict 但其實
    非 strict」的條目，且該鍵永遠不會被降級後的請求命中（快取形同失效）。
    """
    _tmp_cache(monkeypatch, tmp_path)
    calls: list[dict] = []
    monkeypatch.setattr(client, "_complete_effort_safe", _degrading_complete(calls))
    monkeypatch.setattr(client, "_complete", _degrading_complete(calls))
    monkeypatch.setattr(client, "_resolve", lambda: _cfg(base_url=_ARK, model="seed-x"))
    monkeypatch.setattr(client, "_record_usage", lambda *a, **k: None)

    assert client.chat_json("s", "同一則") == {"a": 1}
    n = len(calls)
    assert client.chat_json("s", "同一則") == {"a": 1}
    assert len(calls) == n  # 第二次全靠快取，零 API 呼叫


def test_responses_rung_preserves_strict_and_is_remembered(monkeypatch) -> None:
    """Chat 拒收 strict 但 Responses 支援（實測 Ark seed-2-0-*-260428）→ 改走 Responses 保住 strict。

    這是整條階梯唯一「不放棄 strict」的分支，且結果會被記住，後續呼叫直接走 Responses。
    """
    from app.judge.llm import responses_api

    calls: list[dict] = []

    def _chat_rejects_responses_ok(cfg, kwargs, ck, *a, **k):
        calls.append(dict(kwargs))
        if kwargs.get(responses_api.WIRE_API_KEY) == responses_api.WIRE_RESPONSES:
            return _fake_resp()
        if "response_format" in kwargs:
            raise _sdk_status_err(
                BadRequestError, 400, param="response_format", message="json_schema not supported"
            )
        return _fake_resp()

    monkeypatch.setattr(client, "_complete_effort_safe", _chat_rejects_responses_ok)
    monkeypatch.setattr(client, "_complete", _chat_rejects_responses_ok)
    monkeypatch.setattr(
        client, "_resolve", lambda: _cfg(base_url=_ARK, model="seed-2-0-lite-260428")
    )
    monkeypatch.setattr(client, "_record_usage", lambda *a, **k: None)
    monkeypatch.setattr(client.env, "llm_exact_cache", False)

    client.chat_json("s", "u", schema={"type": "object"})
    # strict 保住了：最後一次呼叫仍帶 json_schema，只是換了 wire API
    assert calls[-1][responses_api.WIRE_API_KEY] == responses_api.WIRE_RESPONSES
    assert calls[-1]["response_format"]["type"] == "json_schema"

    n = len(calls)
    client.chat_json("s", "u2", schema={"type": "object"})
    assert len(calls) - n == 1  # 記憶生效：第二次直接走 Responses，不再探測
    assert calls[-1][responses_api.WIRE_API_KEY] == responses_api.WIRE_RESPONSES


def test_gemini_never_routed_to_responses(monkeypatch) -> None:
    """鐵閘：Gemini 相容層沒有 /responses（實測 404），任何情況都不得被導過去。

    404 不是 400，降級階梯攔不住——一旦誤導流，會以「與結構化輸出完全無關」的面貌炸出來。
    """
    from app.judge.llm import responses_api

    calls: list[dict] = []

    def _always_reject_rf(cfg, kwargs, ck, *a, **k):
        calls.append(dict(kwargs))
        if "response_format" in kwargs:
            raise _sdk_status_err(
                BadRequestError, 400, param="response_format", message="response_format bad"
            )
        return _fake_resp()

    monkeypatch.setattr(client, "_complete_effort_safe", _always_reject_rf)
    monkeypatch.setattr(client, "_complete", _always_reject_rf)
    monkeypatch.setattr(
        client,
        "_resolve",
        lambda: _cfg(base_url="https://generativelanguage.googleapis.com/v1beta/openai"),
    )
    monkeypatch.setattr(client, "_record_usage", lambda *a, **k: None)
    monkeypatch.setattr(client.env, "llm_exact_cache", False)

    client.chat_json("s", "u")
    assert all(responses_api.WIRE_API_KEY not in c for c in calls)  # 一次都沒被標記


def test_dead_wire_marker_never_leaks_to_settled_shape(monkeypatch) -> None:
    """Responses 階失敗時必須清標記——跑批會把收斂形狀發給所有 worker，死標記＝整批走死路。"""
    from app.judge.llm import responses_api

    final: dict = {}

    def _reject_everything_with_rf(cfg, kwargs, ck, *a, **k):
        final.clear()
        final.update(kwargs)
        if "response_format" in kwargs:
            raise _sdk_status_err(
                BadRequestError, 400, param="response_format", message="response_format bad"
            )
        return _fake_resp()

    monkeypatch.setattr(client, "_complete_effort_safe", _reject_everything_with_rf)
    monkeypatch.setattr(client, "_complete", _reject_everything_with_rf)
    monkeypatch.setattr(client, "_resolve", lambda: _cfg(base_url=_ARK, model="seed-x"))
    monkeypatch.setattr(client, "_record_usage", lambda *a, **k: None)
    monkeypatch.setattr(client.env, "llm_exact_cache", False)

    client.chat_json("s", "u")
    assert responses_api.WIRE_API_KEY not in final
