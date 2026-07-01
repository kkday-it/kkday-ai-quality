"""商品分類分組（Tour/Exp/Charter/Tix 對照表）：config/global/product_vertical.json 驅動的純 config loader。

商品分類法屬「前後端共用非機密」商品維度資料（非判決規則），SSOT＝config/global/product_vertical.json，
比照 sources.py 直讀檔（已從 judge_rule_versions 版本化機制解耦，不再走 db.get_rule_active）。內容形態
{group_name: [category_code, ...]}，供歸因列表的商品分類篩選展開代碼。config 線上編輯後呼叫 reload()。
"""

from __future__ import annotations

import json

from app.core.paths import GLOBAL_DIR as _GLOBAL_DIR

_FILE = _GLOBAL_DIR / "product_vertical.json"

# lazy 快取：{group_name: [code]}；None＝未載入。reload() 清空重讀。
_groups: dict[str, list[str]] | None = None


def _load() -> dict[str, list[str]]:
    """lazy 讀 product_vertical.json 的 groups；缺檔 / 壞檔回空 dict（呼叫端安全兜底，不中斷篩選）。"""
    global _groups
    if _groups is None:
        try:
            data = json.loads(_FILE.read_text(encoding="utf-8"))
            _groups = data.get("groups", {}) or {}
        except (OSError, ValueError):
            _groups = {}
    return _groups


def reload() -> None:
    """清快取（product_vertical.json 編輯後呼叫，使新分組即時生效）。"""
    global _groups
    _groups = None


def all_groups() -> dict[str, list[str]]:
    """取商品分類分組定義 {group_name: [category_code, ...]}；缺檔回空 dict。"""
    return dict(_load())


def codes_for_group(group: str) -> list[str]:
    """取某分組（Tour/Exp/Charter/Tix）對應的商品分類代碼清單；分組不存在回空 list。

    Args:
        group: 分組名稱（如 'Tour'）。

    Returns:
        該分組的 CATEGORY_xxx 代碼清單。
    """
    return list(_load().get(group, []))
