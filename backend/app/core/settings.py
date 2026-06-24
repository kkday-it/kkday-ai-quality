"""LLM 模型配置持久化（per-user，存 DB user_settings 表）。

前端「設定」面板管理：provider / model / base_url / api_token / temperature /
thinking / reasoning_effort / provider_models（各供應商自訂 model 清單）。
api_token 絕不明文回前端（masked()）；raw() 供「眼睛顯示全文」。空/遮罩值 save 不覆蓋既有 token。

每個 user 一份設定。judge 路徑（llm client）不傳 user_id，而是透過 contextvar `current()`
取「當前 request 端點注入的 user 設定」——避免污染 pipeline/classify/adequacy 的函式簽名。
"""

from __future__ import annotations

import contextvars

from app.core import db

_DEFAULT: dict = {
    "provider": "openai",          # openai | gemini | azure | custom
    "model": "gpt-5-mini",
    "base_url": "",                # 空＝OpenAI 預設端點
    "api_token": "",
    "temperature": None,           # None＝用 API 預設（gpt-5 系列鎖定不送）
    "thinking": "default",         # default | on | off
    "reasoning_effort": "default", # default | none | low | medium | high | xhigh
    "provider_models": {},         # 各供應商自訂 model 清單（per-user 累積）
}

# 當前 request 生效的 user 設定（端點 Depends 注入）；judge 路徑經 current() 讀取
_current: contextvars.ContextVar[dict | None] = contextvars.ContextVar("current_settings", default=None)


def load_settings(user_id: str) -> dict:
    """讀某 user 完整設定（含明文 token）；未存過回 _DEFAULT 複本。"""
    data = db.load_user_settings(user_id)
    return {**_DEFAULT, **data} if data else dict(_DEFAULT)


def save_settings(user_id: str, patch: dict) -> dict:
    """合併寫入某 user。空或遮罩(*** / …)的 api_token 不覆蓋既有，回 masked()。"""
    cur = load_settings(user_id)
    for k, v in patch.items():
        if k not in _DEFAULT:
            continue
        if k == "api_token" and (not v or "***" in str(v) or "…" in str(v)):
            continue
        cur[k] = v
    db.save_user_settings(user_id, cur)
    return masked(user_id)


def masked(user_id: str) -> dict:
    """回傳給前端：api_token 遮罩，附 has_token 旗標。"""
    cur = load_settings(user_id)
    tok = cur.get("api_token", "") or ""
    cur["api_token"] = (tok[:7] + "…" + tok[-4:]) if len(tok) > 12 else ("***" if tok else "")
    cur["has_token"] = bool(tok)
    return cur


def raw(user_id: str) -> dict:
    """完整未遮罩配置（含明文 api_token）——供設定面板「眼睛顯示全文」。

    ⚠️ 明文回傳 token：僅應在受信任的本地 / 內網環境暴露此端點。
    """
    cur = load_settings(user_id)
    cur["has_token"] = bool(cur.get("api_token"))
    return cur


def set_current(settings: dict) -> None:
    """端點注入當前 request 的 user 設定，供 judge 路徑（llm client）讀取。"""
    _current.set(settings)


def current() -> dict:
    """judge 路徑取當前生效設定；未注入時回 _DEFAULT 複本（→ stub 模式）。"""
    s = _current.get()
    return s if s is not None else dict(_DEFAULT)
