"""商品垂直分類（BD 分工代碼→PM/Vertical 對照表）：rule_code=bd_tag_vertical 的版本化規則 loader。

可編輯版本化：SSOT＝judge_rule_versions（rule_code='bd_tag_vertical'，走 db.get_rule_active），
經「配置」抽屜（商品垂直分類 tab）編輯 / 歷史 / 恢復默認；預設 seed＝config/global/bd_tag_vertical.json，
源自 BD 分工表 Google Sheet（14r0_oZShsX2MiXtwnrd8hAsSMmQJN8hwHqQsOpJ5xaw）。取代舊制 product_vertical
（CATEGORY_xxx→Tour/Exp/Charter/Tix 分組，已於 2026-07-27 全棧退役）。

內容形態 {"pms": [str,...], "verticals": [str,...], "items": {bd_tag_code: {"note": str, "pm": str,
"vertical": str}}}——`pms`/`verticals` 為獨立可配置選項池（設定頁下拉來源，各自可增刪，不隨 items
增減自動變動）；`items` 一代碼對一組 PM+Vertical（非分組容器）。供 reviews 來源補算
vertical/PM 顯示欄，以及歸因列表商品垂直分類篩選展開代碼。即時讀 DB active 版本（存檔後不需手動
reload），查無回空（呼叫端安全兜底，不中斷篩選）。
"""

from __future__ import annotations

RULE_CODE = "bd_tag_vertical"


def _content() -> dict:
    """讀 bd_tag_vertical active 版本 content；缺版本回空 dict。"""
    from app.core import db

    return db.get_rule_active(RULE_CODE) or {}


def _items() -> dict[str, dict[str, str]]:
    """讀 items；缺版本 / 壞資料回空 dict。"""
    items = _content().get("items", {})
    return items if isinstance(items, dict) else {}


def all_items() -> dict[str, dict[str, str]]:
    """取全量 bd_tag 對照 {code: {note, pm, vertical}}；缺版本回空 dict。"""
    return dict(_items())


def pm_for(code: str) -> str | None:
    """取某 bd_tag 代碼對應的 PM 負責人；代碼不存在回 None。"""
    return _items().get(code, {}).get("pm")


def vertical_for(code: str) -> str | None:
    """取某 bd_tag 代碼對應的 Vertical；代碼不存在回 None。"""
    return _items().get(code, {}).get("vertical")


def all_pms() -> list[str]:
    """取 PM 選項池（獨立可配置清單，設定頁下拉來源）；缺版本回空 list。"""
    pms = _content().get("pms", [])
    return list(pms) if isinstance(pms, list) else []


def all_verticals() -> list[str]:
    """取 Vertical 選項池（獨立可配置清單，篩選器/設定頁下拉來源）；缺版本回空 list。

    優先讀取顯式 `verticals` 選項池（設定頁獨立維護，可含尚未指派給任何代碼的新值）；
    舊版本內容缺此欄時回退成從 items 實際值去重推導（向後相容）。
    """
    verticals = _content().get("verticals")
    if isinstance(verticals, list) and verticals:
        return list(verticals)
    derived = {v.get("vertical") for v in _items().values() if v.get("vertical")}
    return sorted(derived)


def codes_for_vertical(vertical: str) -> list[str]:
    """取某 Vertical 對應的 bd_tag 代碼清單（篩選展開用）；Vertical 不存在回空 list。

    Args:
        vertical: Vertical 名稱（如 'Tour'）。

    Returns:
        該 Vertical 底下的 bd_tag 代碼清單。
    """
    return [code for code, v in _items().items() if v.get("vertical") == vertical]
