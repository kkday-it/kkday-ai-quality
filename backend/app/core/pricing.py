"""LLM token → 花費估算（讀 config/global/model_pricing.json）。

判決批量「同步顯示 token 花費金額」用：把 usage（prompt/completion tokens）依模型單價換算 USD。
單價為純資料 SSOT（config/global/model_pricing.json），過時手動更新該檔即時生效（reload() 清快取）。
"""

from __future__ import annotations

import json
from pathlib import Path

# config/ 位於 repo 根：backend/app/core/pricing.py → parents[3] = repo root
_PRICE_FILE = Path(__file__).resolve().parents[3] / "config" / "global" / "model_pricing.json"

_cache: dict | None = None


def _load() -> dict:
    """lazy 載入並快取價目表；缺檔/壞檔回空結構（不中斷判決，花費顯示 0）。"""
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(_PRICE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _cache = {"per_1m": {}, "_default": {"input": 0.0, "output": 0.0}}
    return _cache


def reload() -> None:
    """清快取（model_pricing.json 編輯後呼叫，使新單價即時生效）。"""
    global _cache
    _cache = None


def price_for(model: str) -> dict:
    """模型 → {input, output}（每 1M tokens USD）；未列出回 _default。"""
    cfg = _load()
    return cfg.get("per_1m", {}).get(model) or cfg.get("_default", {"input": 0.0, "output": 0.0})


def cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """token 用量 → 花費 USD（依模型單價；6 位小數）。

    Args:
        model: 模型 id（如 gpt-5-mini）。
        prompt_tokens: 輸入 token 數。
        completion_tokens: 輸出 token 數。

    Returns:
        估算花費 USD（單價缺失時以 _default 計，仍可能為 0）。
    """
    p = price_for(model)
    inp = float(p.get("input", 0.0))
    out = float(p.get("output", 0.0))
    return round(prompt_tokens / 1_000_000 * inp + completion_tokens / 1_000_000 * out, 6)
