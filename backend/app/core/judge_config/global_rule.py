"""整體規則載入器：judge_rule_versions（rule_code='global_rule'，DB）→ 判決全局規則。

SSOT＝DB active 版（`db.get_rule_active('global_rule')`），config/ai_judge/global_rule.json 為初始 seed /
無 DB 版本時 fallback。集中極性閘門（attribute_when）/ 判官提示詞 / abstain 政策 / 證據政策
（含 attr_min_confidence）/ cascade 設定（含 reroute_on_low_conf），供 prejudge 判決主流程引用。
域界線 SSOT＝各 rule_C-N 的 L1 canon（ai_judge.l1_judgment），非本檔——舊 decision_tree /
global_boundaries 已於 2026-07-07 移除（deprecated 無讀取點，歷史內容在 git / DB 舊版本可回溯）。

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


def abstain_policy() -> dict[str, Any]:
    """abstain 政策（l1/l2/l3 各層是否強制/可空）。"""
    return _load().get("abstain_policy", {})


def evidence_policy() -> dict[str, Any]:
    """證據政策（require_quote_grounded / l3_min_confidence / caps）。"""
    return _load().get("evidence_policy", {})


def cascade() -> dict[str, Any]:
    """cascade 設定（enabled + reroute_on_low_conf + stageA_l1 + stageB）。"""
    return _load().get("cascade", {})


def attribution_guidance() -> str:
    """歸因判官提示詞（角色 + 判斷流程指引；不含輸出格式與域界線）。

    判準政策 SSOT：由規則配置頁「global 整體規則」編輯即時生效，取代 prejudge 寫死的 _ATTR_SYS。
    缺值回空字串，由呼叫端回退 code 內 default。域界線/正反例走 ai_judge L1 canon，非此處。
    """
    return _load().get("attribution_guidance", "")


def polarity_guidance() -> str:
    """極性判官提示詞（只判傾向、不歸因）；規則配置頁可編輯，缺值回空由呼叫端回退 default。"""
    return _load().get("polarity_guidance", "")


def polarity_gate() -> dict[str, Any]:
    """極性閘門（attribute_when：哪些整體傾向進歸因）；prejudge._attribute_when 消費。"""
    return _load().get("polarity_gate", {})


def prejudge_depth() -> str:
    """初判歸因深度（"l3"＝完整 L1→L3 cascade；"l2"＝只判 L1+L2）。

    "l2"：初判僅依評論文字，L3 細項常缺商品/訂單佐證而不可靠——改走單呼叫 32 面向目錄
    多歸因（省掉整段 Stage B 選葉），L3 留待接上商品/訂單數據的深判階段補判。
    預設 "l3"（未設＝零行為改變）；非法值回退 "l3"。
    """
    v = str(_load().get("prejudge_depth") or "l3").lower()
    return v if v in ("l2", "l3") else "l3"
