"""整體規則載入器：judge_rule_versions（rule_code='global_rule'，DB）→ 判決全局規則。

SSOT＝DB active 版（`db.get_rule_active('global_rule')`），config/ai_judge/global_rule.json 為初始 seed /
無 DB 版本時 fallback。集中極性閘門 / abstain 政策 / 證據政策 / 六域決策樹 / 跨域界線 / cascade 設定，
供 prejudge 判決主流程引用（取代散落各 rule_C-*.json forbid 與 _ATTR_SYS 的全局規則）。

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


def flow() -> dict[str, Any]:
    """完整判決總規範 dict。"""
    return _load()


def polarity_gate() -> dict[str, Any]:
    """極性閘門設定（attribute_only_when）。"""
    return _load().get("polarity_gate", {})


def abstain_policy() -> dict[str, Any]:
    """abstain 政策（l1/l2/l3 各層是否強制/可空）。"""
    return _load().get("abstain_policy", {})


def evidence_policy() -> dict[str, Any]:
    """證據政策（require_quote_grounded / l3_min_confidence / caps）。"""
    return _load().get("evidence_policy", {})


def decision_tree() -> dict[str, Any]:
    """六域決策樹（order + gates），Stage A 域分類注入源。"""
    return _load().get("decision_tree", {})


def global_boundaries() -> list[str]:
    """跨域界線清單（Stage A 注入輔助）。"""
    return _load().get("global_boundaries", [])


def cascade() -> dict[str, Any]:
    """cascade 設定（enabled + stageA_l1 + stageB）。"""
    return _load().get("cascade", {})
