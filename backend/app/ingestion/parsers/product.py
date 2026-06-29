"""product parser：AI 商品數據原始一列 → product（+ package）。校驗基準。

⚠️ 暫定：依商品 9 邏輯欄位映射；待 AI 商品數據真實格式對齊（只改此 parser + 契約）。
"""

from __future__ import annotations

from typing import Any

from app.ingestion.base import ParsedItem

from app.ingestion.parsers._common import clean, collect_metadata, new_id  # noqa: F401

_PROD_FIELDS = (
    "prod_name",
    "prod_summary",
    "prod_feature",
    "prod_schedules",
    "prod_notice",
    "prod_fee",
    "prod_meetup",
    "prod_redeem",
    "prod_purchase",
)
_USED = {"prod_oid", "pkg_oid", "pkg_desc", "pkg_schedules", "lang_code", *_PROD_FIELDS}


def parse_product(payload: dict[str, Any]) -> ParsedItem:
    data: dict[str, Any] = {
        "prod_oid": str(payload["prod_oid"]),
        "lang": clean(payload.get("lang_code")),
    }
    for f in _PROD_FIELDS:
        data[f] = clean(payload.get(f))
    data["source_metadata"] = collect_metadata(payload, _USED) or None

    children: list[dict[str, Any]] = []
    if clean(payload.get("pkg_oid")):
        children.append(
            {
                "pkg_oid": str(payload["pkg_oid"]),
                "prod_oid": str(payload["prod_oid"]),
                "pkg_desc": clean(payload.get("pkg_desc")),
                "pkg_schedules": clean(payload.get("pkg_schedules")),
            }
        )
    return ParsedItem(kind="product", data=data, children=children)
