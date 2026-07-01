"""review_summary parser：ai_review_summary 原始一列 → signal（聚合，不進 interaction）。"""

from __future__ import annotations

from typing import Any

from app.ingestion.base import ParsedItem
from app.ingestion.parsers._common import clean, new_id, to_float


def parse_review_summary(payload: dict[str, Any]) -> ParsedItem:
    data = {
        "signal_id": new_id(),
        "source": "review_summary",
        "metric_key": clean(payload.get("tag_name")),
        "dimension": "tag_sentiment",
        "dimension_value": clean(payload.get("tag_sentiment")),
        "value": to_float(payload.get("tag_percentage")),
        "prod_oid": clean(payload.get("prod_oid")),
        "payload": {
            "prod_name_zh": clean(payload.get("prod_name_zh")),
            "tag_count": clean(payload.get("tag_count")),
            "positive_count": clean(payload.get("positive_count")),
            "neutral_count": clean(payload.get("neutral_count")),
            "negative_count": clean(payload.get("negative_count")),
        },
    }
    return ParsedItem(kind="signal", data=data)
