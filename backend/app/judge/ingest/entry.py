"""資料錄入：CSV/Excel 批量 + 單個新增 → InboundItem。

容錯各種表頭別名（中英），生成冪等 item_id。供 api 層呼叫後寫入 db。
"""

from __future__ import annotations

import csv
import hashlib
import io
from datetime import datetime, timezone

from app.core.schema import InboundItem

# 欄位別名映射（容錯各種 CSV/Excel 表頭）
FIELD_ALIASES: dict[str, list[str]] = {
    "prod_oid": ["prod_oid", "prodid", "product_id", "商品id", "商品編號", "商品oid"],
    "pkg_oid": ["pkg_oid", "pkgid", "方案id", "方案oid"],
    "rating": ["rating", "score", "評分", "星等", "分數"],
    "comment": [
        "comment",
        "body",
        "content",
        "客訴",
        "差評",
        "評論",
        "內容",
        "問題",
        "text",
        # 售前售後進線（SQL 結果）對話欄位別名
        "aggregated_messages",
        "order_conversation",
        "chatbot_conversation",
        "對話",
        "客服對話",
    ],
}


def _make_id(source: str, prod_oid: str, comment: str) -> str:
    h = hashlib.sha1(f"{source}|{prod_oid}|{comment}".encode()).hexdigest()[:16]
    return f"{source}-{h}"


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _pick(row: dict, key: str) -> str:
    low = {str(k).strip().lower(): v for k, v in row.items() if k is not None}
    for alias in FIELD_ALIASES[key]:
        v = low.get(alias.lower())
        if v not in (None, ""):
            return str(v).strip()
    return ""


def _parse_rating(s: str) -> int | None:
    s = s.strip()
    return int(float(s)) if s.replace(".", "", 1).isdigit() else None


def _row_to_item(row: dict, source: str) -> InboundItem:
    prod_oid = _pick(row, "prod_oid")
    comment = _pick(row, "comment")
    return InboundItem(
        item_id=_make_id(source, prod_oid, comment),
        source=source,
        prod_oid=prod_oid,
        pkg_oid=_pick(row, "pkg_oid"),
        rating=_parse_rating(_pick(row, "rating")),
        comment=comment,
        raw={str(k): v for k, v in row.items()},
        status="pending",
        created_at=_now(),
    )


def parse_csv(content: bytes, source: str = "csv") -> list[InboundItem]:
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    return [_row_to_item(r, source) for r in reader if any(r.values())]


def parse_excel(content: bytes, source: str = "excel") -> list[InboundItem]:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    headers = [str(h).strip() if h is not None else "" for h in rows[0]]
    items: list[InboundItem] = []
    for r in rows[1:]:
        row = {headers[i]: (r[i] if i < len(r) else "") for i in range(len(headers))}
        if any(v not in (None, "") for v in row.values()):
            items.append(_row_to_item(row, source))
    return items


def single_entry(
    prod_oid: str,
    comment: str,
    rating: int | None = None,
    pkg_oid: str = "",
    source: str = "manual",
) -> InboundItem:
    return InboundItem(
        item_id=_make_id(source, prod_oid, comment),
        source=source,
        prod_oid=prod_oid,
        pkg_oid=pkg_oid,
        rating=rating,
        comment=comment,
        raw={},
        status="pending",
        created_at=_now(),
    )
