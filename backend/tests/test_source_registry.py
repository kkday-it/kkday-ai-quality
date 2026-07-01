"""source_registry.spec_for / all_sources 單元測試（無需資料庫，純查表邏輯）。"""

from __future__ import annotations

from app.core import source_registry
from app.core import tables as T


def test_spec_for_product_reviews_returns_correct_spec() -> None:
    """已註冊來源（product_reviews）回傳正確 SourceSpec（表物件 + 自然鍵 + 語意欄名）。"""
    spec = source_registry.spec_for("product_reviews")
    assert spec is not None
    assert spec.source == "product_reviews"
    assert spec.table is T.product_reviews
    assert spec.natural_key == "source_record_id"
    assert spec.score_col == "score"
    assert spec.category_col == "product_category_main"
    assert spec.date_col == "occurred_at"


def test_spec_for_unknown_source_returns_none() -> None:
    """未註冊 / 未拆表的來源回 None（呼叫端 fallback intake_items 舊邏輯）。"""
    assert source_registry.spec_for("unknown_source") is None
    assert source_registry.spec_for("conversations") is None  # 尚未拆表
    assert source_registry.spec_for(None) is None


def test_all_sources_lists_registered_sources() -> None:
    """all_sources 回傳已註冊來源清單，至少含 product_reviews。"""
    assert "product_reviews" in source_registry.all_sources()
