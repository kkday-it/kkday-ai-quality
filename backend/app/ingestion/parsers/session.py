"""session parser：conversations（售前售後客服對話）原始一列 → interaction（對話拆多條 message）。"""

from __future__ import annotations

from typing import Any

from app.ingestion.base import ParsedItem

from app.ingestion.parsers._common import (
    clean,
    collect_metadata,
    content_hash,
    new_id,
    split_conversation,
    to_dt,
)

_USED = {
    "session_oid",
    "prod_oid",
    "order_oid",
    "order_mid",
    "supplier_oid",
    "sessionable_type",
    "aggregated_messages",
    "session_create_date",
}


def parse_session(payload: dict[str, Any]) -> ParsedItem:
    iid = new_id()
    messages, customer_text = split_conversation(payload.get("aggregated_messages"))
    for m in messages:
        m["interaction_id"] = iid
    content = customer_text or None
    data = {
        "interaction_id": iid,
        "source": "session",
        "channel": clean(payload.get("sessionable_type")),  # chatbot / order_message
        "source_record_id": str(payload["session_oid"]),
        "prod_oid": clean(payload.get("prod_oid")),
        "order_oid": clean(payload.get("order_oid")),
        "order_mid": clean(payload.get("order_mid")),
        "supplier_oid": clean(payload.get("supplier_oid")),
        "content": content,
        "occurred_at": to_dt(payload.get("session_create_date")),
        "content_hash": content_hash(content, payload.get("session_oid")),
        "source_metadata": collect_metadata(payload, _USED) or None,
    }
    return ParsedItem(kind="interaction", data=data, children=messages)
