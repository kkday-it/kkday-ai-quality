"""LLM 模型配置持久化（data/settings.json，gitignore）。

前端「設定」面板管理：provider / model / base_url / api_token / temperature / thinking / reasoning_effort。
api_token 絕不明文回傳前端（masked() 遮罩）；空/遮罩值 save 時不覆蓋既有 token。
"""

from __future__ import annotations

import json
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "data" / "settings.json"

_DEFAULT: dict = {
    "provider": "openai",          # openai | gemini | azure | custom
    "model": "gpt-5-mini",
    "base_url": "",                # 空＝OpenAI 預設端點
    "api_token": "",
    "temperature": None,           # None＝用 API 預設（gpt-5 系列鎖定不送）
    "thinking": "default",         # default | on | off
    "reasoning_effort": "default", # default | none | minimal | low | medium | high
}


def load_settings() -> dict:
    if _PATH.exists():
        try:
            return {**_DEFAULT, **json.loads(_PATH.read_text(encoding="utf-8"))}
        except (json.JSONDecodeError, OSError):
            return dict(_DEFAULT)
    return dict(_DEFAULT)


def save_settings(patch: dict) -> dict:
    """合併寫入。空或遮罩(***)的 api_token 不覆蓋既有，避免前端遮罩回傳誤清 key。"""
    cur = load_settings()
    for k, v in patch.items():
        if k not in _DEFAULT:
            continue
        if k == "api_token" and (not v or str(v).startswith("***") or "***" in str(v)):
            continue
        cur[k] = v
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    _PATH.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
    return masked()


def masked() -> dict:
    """回傳給前端：api_token 遮罩，附 has_token 旗標。"""
    cur = dict(load_settings())
    tok = cur.get("api_token", "") or ""
    cur["api_token"] = (tok[:7] + "…" + tok[-4:]) if len(tok) > 12 else ("***" if tok else "")
    cur["has_token"] = bool(tok)
    return cur
