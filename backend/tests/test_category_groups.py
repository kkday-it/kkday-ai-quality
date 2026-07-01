"""category_groups.codes_for_group / all_groups 測試（純 config loader：注入模組快取，不需 DB）。

category_groups 已從 judge_rule_versions 版本化解耦為 config/global/product_vertical.json 直讀，
故測試改以 monkeypatch 注入 loader 快取 `_groups`（取代舊 db.save_rule_version seed），不觸碰 DB。
"""

from __future__ import annotations

from app.core import category_groups


def _seed(monkeypatch, groups: dict) -> None:
    """直接注入 loader 快取（模擬 product_vertical.json 內容），使 _load 不讀真檔。"""
    monkeypatch.setattr(category_groups, "_groups", groups)


def test_codes_for_group_returns_seeded_codes(monkeypatch) -> None:
    """已注入的分組回傳對應代碼清單。"""
    _seed(monkeypatch, {"Tour": ["CATEGORY_019", "CATEGORY_020"], "Tix": ["CATEGORY_002"]})
    assert category_groups.codes_for_group("Tour") == ["CATEGORY_019", "CATEGORY_020"]
    assert category_groups.codes_for_group("Tix") == ["CATEGORY_002"]


def test_codes_for_group_unknown_group_returns_empty(monkeypatch) -> None:
    """查詢不存在的分組名回空清單。"""
    _seed(monkeypatch, {"Tour": ["CATEGORY_019"]})
    assert category_groups.codes_for_group("NotAGroup") == []


def test_codes_for_group_empty_config_returns_empty(monkeypatch) -> None:
    """空 config（缺檔 / 無分組）安全回空清單，不拋錯。"""
    _seed(monkeypatch, {})
    assert category_groups.all_groups() == {}
    assert category_groups.codes_for_group("Tour") == []
