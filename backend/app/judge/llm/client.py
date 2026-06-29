"""LLM client + stub 開關。

配置來源：當前 user 的 DB user_settings（前端「設定」面板管理）優先，fallback 環境變數（config.env）。
無 api_token（settings 與 env 皆無）→ stub 模式（啟發式，零 key 走通 pipeline）；
有 token → OpenAI SDK 真判（預設 gpt-5-mini）。
"""

from __future__ import annotations

import json
import logging

from app.core import settings as _settings
from app.core.config import env

_log = logging.getLogger(__name__)


def _resolve() -> dict:
    """合併當前 request 的 user 設定（contextvar）與 env，回傳實際生效配置。"""
    cfg = _settings.current()
    token = cfg.get("api_token") or env.openai_api_key
    base_url = (cfg.get("base_url") or "").strip()
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

    清單 SSOT＝`config/defaults.json` providers[].defaultModels（前後端共用、按能力排序、含 modelMeta 價格 hint）；
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
    return list(prov.get("defaultModels", [])) if prov else []


def has_key() -> bool:
    return bool(_resolve()["token"])


def is_stub() -> bool:
    return not has_key()


def chat_json(system: str, user: str, stage: str = "default") -> dict:
    """真 LLM 結構化呼叫。配置取自 user_settings（model/base_url/temperature/reasoning）；
    stage 僅作為解析失敗時的 log 標籤（標示是哪個判決階段），不影響生效配置。"""
    from openai import OpenAI

    cfg = _resolve()
    client = (
        OpenAI(api_key=cfg["token"], base_url=cfg["base_url"])
        if cfg["base_url"]
        else OpenAI(api_key=cfg["token"])
    )
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
            "error": "未設定 api_token（stub 模式，無法真打 API）；請先儲存含 token 的配置",
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
