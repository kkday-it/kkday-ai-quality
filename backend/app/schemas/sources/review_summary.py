"""review_summary 源（ai_review_summary CSV / 第二階段 BQ）原始一列契約。

聚合類：商品 × 標籤的情緒統計（非逐筆）→ parser 後進 signal 表，不進 interaction。
冪等鍵為 (prod_oid, tag_name) 複合。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ReviewSummaryRow(BaseModel):
    """ai_review_summary 原始一列。"""

    model_config = ConfigDict(extra="ignore")

    prod_oid: str  # 必填
    prod_name_zh: str | None = None
    tag_name: str  # 必填（與 prod_oid 組成複合冪等鍵）
    tag_count: str | None = None
    positive_count: str | None = None
    neutral_count: str | None = None
    negative_count: str | None = None
    tag_percentage: str | None = None
    tag_sentiment: str | None = None
