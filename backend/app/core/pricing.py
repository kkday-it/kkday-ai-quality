"""LLM token → 花費估算（單價統一內聚於 config/global/default_llm.json 各 model）。

判決批量「同步顯示 token 花費金額」用：把 usage（prompt/completion tokens）依模型單價換算 USD。
單價 SSOT＝default_llm.json `providers[].defaultModels[]` 的 input/output 欄（每 1M tokens USD），
與 model 清單同檔內聚（不再分立 model_pricing.json）；未列單價之模型回退根層 `price_default`。
過時手動更新該檔即時生效（reload() 清快取）。
"""

from __future__ import annotations

import json

from app.core.paths import GLOBAL_DIR  # config/global 目錄（統一定位）

_LLM_FILE = GLOBAL_DIR / "default_llm.json"

# lazy 快取：model id → {input, output}；_default 為未列出模型的回退單價。
_table: dict[str, dict] | None = None
_default: dict = {"input": 0.0, "output": 0.0}


def _load() -> dict[str, dict]:
    """lazy 建 model→單價 索引並快取；缺檔/壞檔回空表（不中斷判決，花費顯示 0）。"""
    global _table, _default
    if _table is None:
        try:
            cfg = json.loads(_LLM_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cfg = {}
        table: dict[str, dict] = {}
        # 從各 provider 的 defaultModels 收斂單價；只收有 input/output 兩欄者（其餘走 _default）。
        for provider in cfg.get("providers", []):
            for m in provider.get("defaultModels", []):
                mid = m.get("id")
                if mid and "input" in m and "output" in m:
                    table[mid] = {"input": float(m["input"]), "output": float(m["output"])}
        _table = table
        _default = cfg.get("price_default") or {"input": 0.0, "output": 0.0}
    return _table


def reload() -> None:
    """清快取（default_llm.json 編輯後呼叫，使新單價即時生效）。"""
    global _table
    _table = None


def price_for(model: str) -> dict:
    """模型 → {input, output}（每 1M tokens USD）；未列出回 price_default。"""
    return _load().get(model) or _default


def cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """token 用量 → 花費 USD（依模型單價；6 位小數）。

    Args:
        model: 模型 id（如 gpt-5-mini）。
        prompt_tokens: 輸入 token 數。
        completion_tokens: 輸出 token 數。

    Returns:
        估算花費 USD（單價缺失時以 price_default 計，仍可能為 0）。
    """
    p = price_for(model)
    inp = float(p.get("input", 0.0))
    out = float(p.get("output", 0.0))
    return round(prompt_tokens / 1_000_000 * inp + completion_tokens / 1_000_000 * out, 6)
