"""LLM client + stub 開關。

配置來源：data/settings.json（前端「設定」面板管理）優先，fallback 環境變數。
無 api_token（settings 與 env 皆無）→ stub 模式（啟發式，零 key 走通 pipeline）；
有 token → OpenAI SDK 真判（預設 gpt-5-mini）。
"""

from __future__ import annotations

import json
import os

from app.core import settings as _settings


def _resolve() -> dict:
    """合併 settings.json 與 env，回傳實際生效配置。"""
    cfg = _settings.load_settings()
    token = cfg.get("api_token") or os.environ.get("OPENAI_API_KEY", "")
    base_url = (cfg.get("base_url") or "").strip()
    model = cfg.get("model") or os.environ.get("AI_JUDGE_MODEL", "gpt-5-mini")
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


def chat_json(system: str, user: str) -> dict:
    """真 LLM 結構化呼叫。配置取自 settings.json（model/base_url/temperature/reasoning）。"""
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
    return json.loads(resp.choices[0].message.content or "{}")
