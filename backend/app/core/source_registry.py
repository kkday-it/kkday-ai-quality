"""反饋來源 → 實體表 registry（product_reviews 拆表後的選表 SSOT）。

5 反饋來源中目前僅 product_reviews 拆為獨立實體表（見 tables.py）；其餘 4 來源
（conversations/freshdesk_tickets/app_feedback/mixpanel_tracker）仍沿用通用
intake_items 表。本模組只登記「已拆表」的來源，未登記者一律 fallback 沿用
intake_items 既有邏輯——刻意不為尚未拆表的來源預先寫死佔位規格（simplicity first，
Rule of Three：待第 2、3 個來源真的拆表時再擴充 registry，非現在假設性預留）。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Table

from app.core import tables as T


@dataclass(frozen=True)
class SourceSpec:
    """已拆表來源的查詢規格：選表 + 各語意欄位在該表的實際欄名。"""

    source: str
    table: Table
    natural_key: str  # 自然鍵欄名（upsert 衝突目標）
    score_col: str | None = None  # 星等/評分欄名（list_problems score 篩選用）
    category_col: str | None = None  # 商品分類欄名（category_group 篩選用）
    date_col: str = "occurred_at"  # 預設日期篩選欄（date_field='occurred_at' 對應）


# 已拆表來源登記（value=source code → SourceSpec）。
_REGISTRY: dict[str, SourceSpec] = {
    "product_reviews": SourceSpec(
        source="product_reviews",
        table=T.product_reviews,
        natural_key="source_record_id",
        score_col="score",
        category_col="product_category_main",
    ),
}


def spec_for(source: str | None) -> SourceSpec | None:
    """依來源 code 取其拆表規格；未拆表 / None / 未知來源一律回 None（呼叫端 fallback 舊邏輯）。

    Args:
        source: 來源 code（如 'product_reviews'）；None 表示不限定來源。

    Returns:
        該來源的 SourceSpec；未命中回 None。
    """
    if not source:
        return None
    return _REGISTRY.get(source)


def all_sources() -> list[str]:
    """已註冊（已拆表）的來源 code 清單。"""
    return list(_REGISTRY.keys())
