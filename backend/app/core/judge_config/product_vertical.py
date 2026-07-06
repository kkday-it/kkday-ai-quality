"""商品垂直分類（Tour/Exp/Charter/Tix 對照表）：rule_code=product_vertical 的版本化規則 loader。

可編輯版本化：SSOT＝judge_rule_versions（rule_code='product_vertical'，走 db.get_rule_active），
經「配置」抽屜（商品垂直分類 tab）編輯 / 歷史 / 恢復默認；預設 seed＝config/global/product_vertical.json。
內容形態 {"groups": {group_name: [category_code, ...]}}，供歸因列表的商品垂直分類篩選展開代碼。
即時讀 DB active 版本（存檔後不需手動 reload），查無回空 dict（呼叫端安全兜底，不中斷篩選）。
"""

from __future__ import annotations

RULE_CODE = "product_vertical"


def _groups() -> dict[str, list[str]]:
    """讀 product_vertical active 版本的 groups；缺版本 / 壞資料回空 dict。"""
    from app.core import db

    content = db.get_rule_active(RULE_CODE) or {}
    groups = content.get("groups", {})
    return groups if isinstance(groups, dict) else {}


def all_groups() -> dict[str, list[str]]:
    """取商品垂直分類定義 {group_name: [category_code, ...]}；缺版本回空 dict。"""
    return dict(_groups())


def codes_for_group(group: str) -> list[str]:
    """取某分組（Tour/Exp/Charter/Tix）對應的商品分類代碼清單；分組不存在回空 list。

    Args:
        group: 分組名稱（如 'Tour'）。

    Returns:
        該分組的 CATEGORY_xxx 代碼清單。
    """
    return list(_groups().get(group, []))


def group_order() -> list[str]:
    """分組顯示順序：content.group_order 顯式排序欄為準（jsonb 不保 object key 序），
    過濾已刪分組 + 補掛不在 order 內的新分組；舊版本內容缺欄時回退 groups keys（jsonb 序）。
    """
    from app.core import db

    content = db.get_rule_active(RULE_CODE) or {}
    keys = list(_groups().keys())
    raw = content.get("group_order")
    order = [g for g in raw if g in keys] if isinstance(raw, list) else []
    return order + [g for g in keys if g not in order]
