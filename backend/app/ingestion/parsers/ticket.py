"""ticket parser：freshdesk_tickets 原始一列 → interaction（product_id 映射 prod_oid）。"""

from __future__ import annotations

from typing import Any

from app.ingestion.base import ParsedItem
from app.ingestion.parsers._common import (
    clean,
    collect_metadata,
    content_hash,
    new_id,
    to_dt,
    to_float,
)

_USED = {
    "id",
    "product_id",
    "subject",
    "description",
    "st_survey_rating",
    "source_name",
    "created_at",
}


def parse_ticket(payload: dict[str, Any]) -> ParsedItem:
    iid = new_id()
    content = clean(payload.get("description"))
    data = {
        "interaction_id": iid,
        "source": "ticket",
        "channel": clean(payload.get("source_name")),
        "source_record_id": str(payload["id"]),
        "prod_oid": clean(payload.get("product_id")),  # 欄位映射 product_id → prod_oid
        "title": clean(payload.get("subject")),
        "content": content,
        "score": to_float(payload.get("st_survey_rating")),
        "occurred_at": to_dt(payload.get("created_at")),
        "content_hash": content_hash(content, payload.get("id")),
        "source_metadata": collect_metadata(payload, _USED) or None,  # notes/tags/status 等留存
    }
    messages = [
        {
            "message_id": new_id(),
            "interaction_id": iid,
            "author_role": "customer",
            "text": content,
            "seq": 0,
        }
    ]
    return ParsedItem(kind="interaction", data=data, children=messages)
