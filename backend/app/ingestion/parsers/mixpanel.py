"""mixpanel parser：mixpanel 原始一列 → signal（埋點計數聚合，不進 interaction）。"""

from __future__ import annotations

from typing import Any

from app.ingestion.base import ParsedItem

from app.ingestion.parsers._common import clean, new_id, to_float


def parse_mixpanel(payload: dict[str, Any]) -> ParsedItem:
    data = {
        "signal_id": new_id(),
        "source": "mixpanel",
        "metric_key": clean(payload.get("event")),
        "dimension": clean(payload.get("breakdown_property")),
        "dimension_value": clean(payload.get("breakdown_value")),
        "value": to_float(payload.get("count")),
        "prod_oid": None,  # mixpanel 埋點無商品關聯
        "payload": {"event_total": clean(payload.get("event_total"))},
    }
    return ParsedItem(kind="signal", data=data)
