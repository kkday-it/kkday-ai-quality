"""反饋來源 → 實體表 registry（來源選表 SSOT）。

5 反饋來源皆已拆為獨立實體表（見 tables.py）：product_reviews / conversations /
freshdesk_tickets / app_feedback / mixpanel_tracker，各以特徵 id 為 natural_key。
本模組登記每個來源的 table + natural_key + score_col/category_col/date_col，供 db.py
統一 spec 驅動查詢（source=None＝縱覽全部，走 judgments 直接聚合，非單表）。
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
    category_col: str | None = None  # 商品分類欄名（product_vertical 篩選用）
    date_col: str = "occurred_at"  # 預設日期篩選欄（date_field='occurred_at' 對應）


# 5 反饋來源登記（value=source code → SourceSpec）。各表對齊源 schema、PK=特徵 id；
# canonical 顯示欄映射走 config/ai_judge/source_mapping.json 的 field_map（源欄→canonical）。
# score_col/category_col/date_col 為「該來源實際源欄名」（供 list score 篩選 / vertical 篩選 / 日期排序）。
_REGISTRY: dict[str, SourceSpec] = {
    "product_reviews": SourceSpec(
        source="product_reviews",
        table=T.product_reviews,
        natural_key="rec_oid",
        score_col="rec_scores",
        category_col="product_category",
        date_col="create_date",
    ),
    "conversations": SourceSpec(
        source="conversations",
        table=T.conversations,
        natural_key="session_oid",
        date_col="session_create_date",
    ),
    "freshdesk_tickets": SourceSpec(
        source="freshdesk_tickets",
        table=T.freshdesk_tickets,
        natural_key="id",
        score_col="st_survey_rating",
        date_col="created_at",
    ),
    "app_feedback": SourceSpec(
        source="app_feedback",
        table=T.app_feedback,
        natural_key="oid",
        score_col="score",
        date_col="created_datetime",
    ),
    "mixpanel_tracker": SourceSpec(
        source="mixpanel_tracker",
        table=T.mixpanel_tracker,
        natural_key="insert_id",
        date_col="time",
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
