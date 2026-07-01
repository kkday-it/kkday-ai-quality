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
# sink 簽名：(model:str, prompt_tokens:int, completion_tokens:int) → None（須自行 thread-safe）。
_usage_sink: ContextVar[Callable[[str, int, int], None] | None] = ContextVar(
    "llm_usage_sink", default=None
)


def set_usage_sink(cb: Callable[[str, int, int], None] | None) -> None:
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
            kwargs: dict = {"api_key": token, "max_retries": 5, "timeout": float(env.llm_timeout)}
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


def list_models() -> list[str]:
    """回傳當前 provider（由 base_url 判定）的本地預設模型清單；不再打 /v1/models。

    清單 SSOT＝`config/global/default_llm.json` providers[].defaultModels（前後端共用、{id,desc} 物件、按能力排序）；
    新增模型只改該檔一處。改本地預設原因：/v1/models 會倒出帳號全模型（embedding / 語音 / 影像 /
    legacy davinci-babbage-ada / ft-kkday 舊 fine-tune），下拉可能誤選 whisper 當判決模型。
    base_url 空 → 預設 openai；非 OpenAI 依關鍵字判 gemini / bytedance。
    """
    base = (_resolve().get("base_url") or "").strip()
    providers = _settings.LLM_PROVIDERS
    prov = None
    if base:
        prov = next((p for p in providers if p.get("base_url") == base), None)
        if prov is None:
            if "generativelanguage" in base:
                prov = next((p for p in providers if p.get("id") == "gemini"), None)
            elif "bytepluses" in base or "volces" in base:
                prov = next((p for p in providers if p.get("id") == "bytedance"), None)
        if prov is None:
            return []  # 自訂 base_url 無對應 provider → 回空，前端顯示「請手動輸入 model」（勿誤導回 OpenAI 清單）
    if prov is None:  # base_url 空 → 預設 openai
        prov = next((p for p in providers if p.get("id") == "openai"), None)
    return [m["id"] for m in prov.get("defaultModels", [])] if prov else []


def has_key() -> bool:
    return bool(_resolve()["token"])


def is_stub() -> bool:
    return not has_key()


def chat_json(system: str, user: str, stage: str = "default") -> dict:
    """真 LLM 結構化呼叫。配置取自 user_settings（model/base_url/temperature/reasoning）；
    stage 僅作為解析失敗時的 log 標籤（標示是哪個判決階段），不影響生效配置。"""
    cfg = _resolve()
    client = _get_client(cfg["token"], cfg["base_url"])  # 共用快取 client（含 retry/timeout）
    kwargs: dict = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
    }
    # gpt-5 系列 temperature 鎖定：僅非 None 時送（見 gpt5-temperature-locked）
    if cfg["temperature"] is not None:
        kwargs["temperature"] = float(cfg["temperature"])
    eff = cfg["reasoning_effort"]
    if eff and eff != "default":
        kwargs["reasoning_effort"] = eff
    resp = client.chat.completions.create(**kwargs)
    # token 用量回報（供批量累計花費；失敗不影響判決）
    sink = _usage_sink.get()
    usage = getattr(resp, "usage", None)
    if sink and usage:
        try:
            sink(
                cfg["model"],
                int(getattr(usage, "prompt_tokens", 0) or 0),
                int(getattr(usage, "completion_tokens", 0) or 0),
            )
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
