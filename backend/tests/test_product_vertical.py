"""product_vertical.codes_for_group / all_groups 測試（版本化規則 loader：monkeypatch db.get_rule_active）。

product_vertical 為可編輯版本化規則（rule_code='product_vertical'，走 judge_rule_versions），
loader 即時讀 db.get_rule_active('product_vertical')；測試以 monkeypatch 注入 active 內容，不觸碰真 DB。
"""

from __future__ import annotations

from app.core import db, product_vertical


def _seed(monkeypatch, groups: dict) -> None:
    """注入 db.get_rule_active('product_vertical') 回傳 {"groups": ...}，模擬 active 版本內容。"""
    monkeypatch.setattr(
        db,
        "get_rule_active",
        lambda code: {"groups": groups} if code == "product_vertical" else None,
    )


def test_codes_for_group_returns_seeded_codes(monkeypatch) -> None:
    """已注入的分組回傳對應代碼清單。"""
    _seed(monkeypatch, {"Tour": ["CATEGORY_019", "CATEGORY_020"], "Tix": ["CATEGORY_002"]})
    assert product_vertical.codes_for_group("Tour") == ["CATEGORY_019", "CATEGORY_020"]
    assert product_vertical.codes_for_group("Tix") == ["CATEGORY_002"]


def test_codes_for_group_unknown_group_returns_empty(monkeypatch) -> None:
    """查詢不存在的分組名回空清單。"""
    _seed(monkeypatch, {"Tour": ["CATEGORY_019"]})
    assert product_vertical.codes_for_group("NotAGroup") == []


def test_codes_for_group_empty_config_returns_empty(monkeypatch) -> None:
    """無 active 版本（缺規則）安全回空清單，不拋錯。"""
    monkeypatch.setattr(db, "get_rule_active", lambda code: None)
    assert product_vertical.all_groups() == {}
    assert product_vertical.codes_for_group("Tour") == []
