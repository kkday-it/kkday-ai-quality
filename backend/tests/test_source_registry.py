"""source_registry.spec_for 單元測試（無需資料庫，純查表邏輯）。"""

from __future__ import annotations

import pytest

from app.core.db import source_registry
from app.core.db import tables as T


def test_spec_for_reviews_returns_correct_spec() -> None:
    """已註冊來源（reviews）回傳正確 SourceSpec（表物件 + 自然鍵 + 語意欄名）。"""
    spec = source_registry.spec_for("reviews")
    assert spec is not None
    assert spec.source == "reviews"
    assert spec.table is T.reviews
    assert spec.natural_key == "rec_oid"
    assert spec.score_col == "rec_scores"
    assert spec.bd_tag_col == "bd_tag_cd"  # 篩選鍵一律指向代碼欄（bd_tag 為中文文字）
    assert spec.date_col == "create_date"


def test_bd_tag_col_is_code_column_across_sources() -> None:
    """兩來源的 bd_tag_col 皆為 bd_tag_cd（代碼欄）——BQ 取數 SQL 已統一命名，不可誤指向中文欄。"""
    for source in ("reviews", "conversations"):
        spec = source_registry.spec_for(source)
        assert spec is not None
        assert spec.bd_tag_col == "bd_tag_cd", source


@pytest.mark.parametrize(
    ("source", "natural_key"),
    [
        ("reviews", "rec_oid"),
        ("conversations", "session_oid"),
        ("freshdesk_tickets", "id"),
        ("app_feedback", "oid"),
        ("mixpanel_tracker", "insert_id"),
    ],
)
def test_spec_for_registered_sources(source: str, natural_key: str) -> None:
    """5 來源皆已拆表註冊，各回對應 SourceSpec（自然鍵對齊該表特徵 id）。"""
    spec = source_registry.spec_for(source)
    assert spec is not None
    assert spec.source == source
    assert spec.natural_key == natural_key


def test_spec_for_unknown_source_returns_none() -> None:
    """未知來源 / None 回 None（source=None 即縱覽全部，走 attributions 直接聚合）。"""
    assert source_registry.spec_for("unknown_source") is None
    assert source_registry.spec_for(None) is None
