"""category_groups.codes_for_group / all_groups 測試（需 temp_db：讀 judge_rule_versions 的 active 版）。"""

from __future__ import annotations

from app.core import category_groups, db


def _seed(content: dict) -> None:
    """存一版 category_groups 規則並設為 active（沿用既有 save_rule_version 機制）。"""
    db.save_rule_version("category_groups", content, note="test seed", author="test")


def test_codes_for_group_returns_seeded_codes(temp_db) -> None:
    """已 seed 的分組回傳對應代碼清單。"""
    _seed({"groups": {"Tour": ["CATEGORY_019", "CATEGORY_020"], "Tix": ["CATEGORY_002"]}})
    assert category_groups.codes_for_group("Tour") == ["CATEGORY_019", "CATEGORY_020"]
    assert category_groups.codes_for_group("Tix") == ["CATEGORY_002"]


def test_codes_for_group_unknown_group_returns_empty(temp_db) -> None:
    """已 seed 但查詢不存在的分組名回空清單。"""
    _seed({"groups": {"Tour": ["CATEGORY_019"]}})
    assert category_groups.codes_for_group("NotAGroup") == []


def test_codes_for_group_no_active_rule_returns_empty(temp_db) -> None:
    """尚未 seed 任何 active 版時（DB 全空）安全回空清單，不拋錯。"""
    assert category_groups.all_groups() == {}
    assert category_groups.codes_for_group("Tour") == []
