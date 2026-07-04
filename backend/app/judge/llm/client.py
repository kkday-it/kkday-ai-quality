"""LLM client + stub 開關。

配置來源：當前 user 的 DB user_settings（前端「設定」面板管理）優先，fallback 環境變數（config.env）。
無 api_token（settings 與 env 皆無）→ stub 模式（啟發式，零 key 走通 pipeline）；
有 token → OpenAI SDK 真判（預設 gpt-5-mini）。
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from contextvars import ContextVar

from app.core import settings as _settings
from app.core.config import env

_log = logging.getLogger(__name__)

# token 用量回報槽（ContextVar）：批量判決設定 sink，chat_json 每次呼叫把 usage 回報以累計花費。
# 用 ContextVar 是因 prejudge_batch 以 copy_context 派工，worker 自動繼承 _run 設的 sink。
# sink 簽名：(model, prompt_tokens, completion_tokens, cached_tokens) → None（須自行 thread-safe）；
# cached_tokens＝prompt_tokens 中命中 prompt cache 的部分，供折扣計價。
_usage_sink: ContextVar[Callable[[str, int, int, int], None] | None] = ContextVar(
    "llm_usage_sink", default=None
)


def set_usage_sink(cb: Callable[[str, int, int, int], None] | None) -> None:
    """設定當前 context 的 token 用量回報 sink（批量判決用；None＝不回報）。"""
    _usage_sink.set(cb)


# 共用 OpenAI client 快取（按 token+base_url）：避免每次呼叫新建 connection pool；
# OpenAI SDK client thread-safe，可跨 ThreadPool worker 共用。高併發批量必要的效能優化。
_CLIENT_CACHE: dict[tuple[str, str], object] = {}
_CLIENT_LOCK = threading.Lock()


def _get_client(token: str, base_url: str):
    """取（或建並快取）OpenAI client；附 max_retries（429/5xx 指數退避）+ timeout。

    Args:
        token: API key。
        base_url: 自訂 endpoint（空＝OpenAI 官方）。

    Returns:
        OpenAI client 實例（同 token+base_url 重用，連線池共享）。
    """
    from openai import OpenAI

    key = (token or "", base_url or "")
    with _CLIENT_LOCK:
        cli = _CLIENT_CACHE.get(key)
        if cli is None:
            kwargs: dict = {
                "api_key": token,
                "max_retries": env.llm_max_retries,
                "timeout": float(env.llm_timeout),
            }
            if base_url:
                kwargs["base_url"] = base_url
            cli = OpenAI(**kwargs)
            _CLIENT_CACHE[key] = cli
        return cli


def _resolve() -> dict:
    """合併當前 request 的 user 設定（contextvar）與 env，回傳實際生效配置。

    token 取「當前 provider（由 base_url 反推）對應的 provider_tokens 條目」，fallback env；
    確保 token 永遠對齊當前 base_url 的 provider，不會用到別家 provider 的 key。
    """
    cfg = _settings.current()
    base_url = (cfg.get("base_url") or "").strip()
    provider = _settings.provider_id_for(base_url)
    token = (cfg.get("provider_tokens") or {}).get(provider) or env.openai_api_key
    model = cfg.get("model") or env.ai_judge_model
    return {
        "token": token,
        "base_url": base_url,
        "model": model,
        "temperature": cfg.get("temperature"),
        "reasoning_effort": cfg.get("reasoning_effort", "default"),
    }


def has_key() -> bool:
    return bool(_resolve()["token"])


def is_stub() -> bool:
    return not has_key()


def chat_json(
    system: str,
    user: str,
    stage: str = "default",
    *,
    schema: dict | None = None,
    cache_key: str | None = None,
) -> dict:
    """真 LLM 結構化呼叫。配置取自 user_settings（model/base_url/temperature/reasoning）；
    stage 僅作為解析失敗時的 log 標籤（標示是哪個判決階段），不影響生效配置。

    Args:
        schema: 傳入時用 OpenAI Structured Outputs（response_format=json_schema, strict）——
            生成階段即 token-level 保證輸出符合此 JSON Schema（如 l3_code enum 只吐合法 code）。
            不支援 json_schema 的 provider（回 400）自動回退 json_object（事後由白名單校驗）。
            None＝維持 json_object（極性等不需 enum 的階段）。
        cache_key: OpenAI prompt caching 路由提示（`prompt_cache_key`），把相同前綴的呼叫導向同一
            伺服器提升命中率。**僅 OpenAI 支援**此參數，故依 provider（base_url 反推）判斷才帶，
            避免相容端點（Gemini/ByteDance）拒收。實際命中仍靠「靜態前綴放前、動態放後」的排序。
    """
    cfg = _resolve()
    client = _get_client(cfg["token"], cfg["base_url"])  # 共用快取 client（含 retry/timeout）
    kwargs: dict = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if cache_key and _settings.provider_id_for(cfg["base_url"]) == "openai":
        kwargs["prompt_cache_key"] = cache_key
    if schema is not None:
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "attribution", "strict": True, "schema": schema},
        }
    else:
        kwargs["response_format"] = {"type": "json_object"}
    # gpt-5 系列 temperature 鎖定：僅非 None 時送（見 gpt5-temperature-locked）
    if cfg["temperature"] is not None:
        kwargs["temperature"] = float(cfg["temperature"])
    eff = cfg["reasoning_effort"]
    if eff and eff != "default":
        kwargs["reasoning_effort"] = eff
    try:
        resp = client.chat.completions.create(**kwargs)
    except Exception as e:  # noqa: BLE001  json_schema 不受支援（400）→ 回退 json_object 重試一次
        if schema is None:
            raise
        _log.warning("json_schema 不受支援(stage=%s)，回退 json_object：%s", stage, str(e).splitlines()[0][:160])
        kwargs["response_format"] = {"type": "json_object"}
        resp = client.chat.completions.create(**kwargs)
    # token 用量回報（供批量累計花費；失敗不影響判決）
    sink = _usage_sink.get()
    usage = getattr(resp, "usage", None)
    if usage:
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        # prompt caching 命中：cached_tokens 非零代表靜態前綴（判準法典）已重用、input 計費打折
        details = getattr(usage, "prompt_tokens_details", None)
        cached = int(getattr(details, "cached_tokens", 0) or 0) if details else 0
        if cached:
            _log.info("prompt cache 命中 stage=%s cached=%d/%d", stage, cached, prompt_tokens)
        if sink:
            try:
                sink(cfg["model"], prompt_tokens, completion_tokens, cached)
            except Exception:  # noqa: BLE001  計費僅輔助，絕不阻斷判決
                _log.debug("usage sink 回報失敗 stage=%s", stage)
    raw = (resp.choices[0].message.content or "{}") if resp.choices else "{}"
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # LLM 回非 JSON（空字串 / markdown fence 包裹 / prompt 漂移）→ 記錄並降級為空 dict，
        # 由上層 _sanitize 補位（不靜默吞：留 log 供 monitoring 偵測模型輸出退化）。
        _log.warning("LLM JSON parse 失敗 stage=%s model=%s raw=%r", stage, cfg["model"], raw[:200])
        return {}


def ping(prompt: str = "回覆 OK", cfg: dict | None = None) -> dict:
    """測試連線：送一個極短 prompt，回傳終端機顯示用的 I/O。

    cfg=None → 用當前生效（已儲存）設定；傳入 cfg（token/base_url/model/temperature/reasoning_effort）
    → 即時測「當前表單值」不經儲存。不丟例外（錯誤收進 error）。
    回 {ok, model, base_url, sent, reply, latency_ms, tokens, error}；無 token → ok=False。
    """
    import time

    cfg = cfg or _resolve()
    base = cfg["base_url"] or "https://api.openai.com/v1"
    if not cfg["token"]:
        return {
            "ok": False,
            "model": cfg["model"],
            "base_url": base,
            "sent": prompt,
            "error": "當前 provider 未設定 token（stub 模式，無法真打 API）；請先填入並儲存該 provider 的 token",
        }

    from openai import OpenAI

    client = (
        OpenAI(api_key=cfg["token"], base_url=cfg["base_url"])
        if cfg["base_url"]
        else OpenAI(api_key=cfg["token"])
    )
    kwargs: dict = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": "你是連線測試助手，只需極簡短回覆。"},
            {"role": "user", "content": prompt},
        ],
    }
    if cfg["temperature"] is not None:
        kwargs["temperature"] = float(cfg["temperature"])
    eff = cfg["reasoning_effort"]
    if eff and eff != "default":
        kwargs["reasoning_effort"] = eff

    t0 = time.monotonic()
    try:
        resp = client.chat.completions.create(**kwargs)
        dt = int((time.monotonic() - t0) * 1000)
        usage = getattr(resp, "usage", None)
        return {
            "ok": True,
            "model": cfg["model"],
            "base_url": base,
            "sent": prompt,
            "reply": (resp.choices[0].message.content or "").strip(),
            "latency_ms": dt,
            "tokens": getattr(usage, "total_tokens", None) if usage else None,
        }
    except Exception as e:  # 只回錯誤首行（避免洩漏 key / 堆疊）
        dt = int((time.monotonic() - t0) * 1000)
        return {
            "ok": False,
            "model": cfg["model"],
            "base_url": base,
            "sent": prompt,
            "error": str(e).splitlines()[0][:300],
            "latency_ms": dt,
        }
