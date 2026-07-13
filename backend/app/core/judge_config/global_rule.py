"""整體規則載入器：judge_rule_versions（rule_code='global_rule'，DB）→ 判決全局規則。

SSOT＝DB active 版（`db.get_rule_active('global_rule')`），config/ai_judge/global_rule.json 為初始 seed /
無 DB 版本時 fallback。集中極性閘門（attribute_when）/ 證據政策（含 attr_min_confidence），供 prejudge
判決主流程引用。判官提示詞與域界線 SSOT＝docs/prompts/prompts/*.md（Prompt-as-Source 架構，見
app.judge.prompt_source）——2026-07-13 隨 JSON 樹全面退役，attribution_guidance/polarity_guidance/
abstain_policy/cascade/prejudge_depth 已隨之移除（判準與流程深度已 100% 由 prompt 決定，非本檔）。

快取：模組級 lazy 快取（判決熱路徑高頻讀）；規則寫入（存檔 / 恢復默認 / 恢復版本）後由 rules.py 呼叫
reload() 重載，使新規則即時生效——與 ai_judge 同策略。
"""

from __future__ import annotations

from typing import Any

RULE_CODE = "global_rule"

# 模組級快取（lazy；reload() 清空重建）。
_cache: dict[str, Any] | None = None


def _load() -> dict[str, Any]:
    """lazy 載入 global_rule active content（DB 優先，缺版本回退 seed 檔）；壞檔/缺檔回空 dict。"""
    global _cache
    if _cache is None:
        from app.core import db  # lazy：避免 import-time 拉 sqlalchemy；db 不 import 本模組故無循環

        data = db.get_rule_active(RULE_CODE)
        if data is None:
            try:
                data = db.default_rule_content(RULE_CODE)
            except (FileNotFoundError, ValueError):
                data = {}
        _cache = data or {}
    return _cache


def reload() -> None:
    """清快取（規則寫入後呼叫，使新總規範即時反映於判決）。"""
    global _cache
    _cache = None


def evidence_policy() -> dict[str, Any]:
    """證據政策（require_quote_grounded / attr_min_confidence / secondary_min_confidence）。"""
    return _load().get("evidence_policy", {})


def polarity_gate() -> dict[str, Any]:
    """極性閘門（attribute_when：哪些整體傾向進歸因）；prejudge._attribute_when 消費。"""
    return _load().get("polarity_gate", {})
