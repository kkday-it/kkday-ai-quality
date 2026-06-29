"""feedback parser：app_feedback 原始一列 → interaction（無關聯鍵 → link 步驟標 pending）。"""

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

_USED = {"oid", "comment", "score", "source", "lang_code", "created_datetime"}


def parse_feedback(payload: dict[str, Any]) -> ParsedItem:
    iid = new_id()
    content = clean(payload.get("comment"))
    data = {
        "interaction_id": iid,
        "source": "feedback",
        "channel": clean(payload.get("source")),  # IOS / Android
        "source_record_id": str(payload["oid"]),
        "content": content,
        "score": to_float(payload.get("score")),
        "lang": clean(payload.get("lang_code")),
        "occurred_at": to_dt(payload.get("created_datetime")),
        "content_hash": content_hash(content, payload.get("oid")),
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
