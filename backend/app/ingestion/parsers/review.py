"""review parser：product_reviews 原始一列 → interaction（+ 1 條 customer message）。"""

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
    "rec_oid",
    "prod_oid",
    "pkg_oid",
    "order_oid",
    "order_mid",
    "supplier_oid",
    "member_uuid",
    "rec_title",
    "rec_desc",
    "rec_scores",
    "lang_code",
    "create_date",
}


def parse_review(payload: dict[str, Any]) -> ParsedItem:
    iid = new_id()
    content = clean(payload.get("rec_desc"))
    data = {
        "interaction_id": iid,
        "source": "review",
        "channel": "review",
        "source_record_id": str(payload["rec_oid"]),
        "prod_oid": clean(payload.get("prod_oid")),
        "pkg_oid": clean(payload.get("pkg_oid")),
        "order_oid": clean(payload.get("order_oid")),
        "order_mid": clean(payload.get("order_mid")),
        "supplier_oid": clean(payload.get("supplier_oid")),
        "member_uuid": clean(payload.get("member_uuid")),
        "title": clean(payload.get("rec_title")),
        "content": content,
        "score": to_float(payload.get("rec_scores")),
        "lang": clean(payload.get("lang_code")),
        "occurred_at": to_dt(payload.get("create_date")),
        "content_hash": content_hash(content, payload.get("rec_oid")),
        "source_metadata": collect_metadata(payload, _USED) or None,
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
