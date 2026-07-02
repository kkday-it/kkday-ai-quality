"""資料錄入：CSV/Excel 批量 + 單個新增 → InboundItem。

容錯各種表頭別名（中英），生成冪等 item_id。供 api 層呼叫後寫入 db。
"""

from __future__ import annotations

import csv
import hashlib
import io
from datetime import date, datetime, time

from app.core.schema import InboundItem
from app.core.utils import now_iso as _now


def _cell(value):
    """xlsx 儲存格值正規化：日期時間→字串（對齊 CSV 全字串語義，避免 datetime 無法 JSON 序列化），None→空字串。

    openpyxl `data_only=True` 會把 Excel 日期格回傳成 datetime/date/time 物件；下游 json.dumps(raw)
    無法序列化 → 整列轉換 TypeError。此處在讀取邊界統一轉字串，使 xlsx 與 CSV 行為一致。
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, (date, time)):
        return value.isoformat()
    return value

# 欄位別名映射（容錯各種 CSV/Excel 表頭）
FIELD_ALIASES: dict[str, list[str]] = {
    # product_id/prod_mid 為工單/埋點商品欄；rec 無獨立 prod 別名（用 prod_oid 本名）
    "prod_oid": ["prod_oid", "prodid", "product_id", "prod_mid", "商品id", "商品編號", "商品oid"],
    "pkg_oid": ["pkg_oid", "pkgid", "方案id", "方案oid"],
    # rec_scores=商品評論星等；st_survey_rating=工單滿意度評分
    "rating": ["rating", "score", "rec_scores", "st_survey_rating", "評分", "星等", "分數"],
    "comment": [
        # 商品評論：rec_desc 為評論本文（rec_title 標題保留於 raw，不覆蓋本文）
        "rec_desc",
        # 工單：description 為內文本體（優先於短標題 subject）
        "description",
        "subject",
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


# ── 多工作表 / config 驅動上傳（5 源自動辨識路徑；與上方 review 別名路徑並存）──────────


def read_sheets(content: bytes, filename: str) -> list[dict]:
    """讀 CSV/Excel → 工作表清單。CSV 視為單表（sheet_name=檔名）；xlsx 遍歷所有分頁。

    供上傳「乾跑校驗」與「確認匯入」共用：先取每表 headers 做來源辨識，再逐列正規化。

    Args:
        content: 檔案 bytes。
        filename: 原始檔名（決定 CSV/Excel 與單表命名）。

    Returns:
        [{sheet_name, headers: list[str], rows: list[dict]}]；空表略過。

    Raises:
        ValueError: 副檔名非 .csv/.xlsx/.xls。
    """
    name = (filename or "").lower()
    if name.endswith(".csv"):
        text = content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        headers = [h for h in (reader.fieldnames or []) if h is not None]
        rows = [dict(r) for r in reader if any((v or "").strip() for v in r.values())]
        stem = filename.rsplit("/", 1)[-1].rsplit(".", 1)[0] or "sheet"
        return [{"sheet_name": stem, "headers": headers, "rows": rows}]
    if name.endswith((".xlsx", ".xls")):
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        out: list[dict] = []
        for ws in wb.worksheets:
            raw_rows = list(ws.iter_rows(values_only=True))
            if not raw_rows:
                continue
            headers = [str(h).strip() if h is not None else "" for h in raw_rows[0]]
            rows = []
            for r in raw_rows[1:]:
                row = {headers[i]: (_cell(r[i]) if i < len(r) else "") for i in range(len(headers))}
                if any(v not in (None, "") for v in row.values()):
                    rows.append(row)
            out.append({"sheet_name": ws.title, "headers": [h for h in headers if h], "rows": rows})
        return out
    raise ValueError("只支援 .csv / .xlsx / .xls")


def item_from_canonical(canon: dict, raw: dict) -> InboundItem:
    """source_mapping.normalize_row 的 canonical 輸出 → InboundItem（冪等 by source_record_id）。

    item_id 以 source + source_record_id 生成（同筆重上傳覆蓋）；無 source_record_id 時退回內容雜湊。
    raw 保留原始整列（Phase C 升 canonical 欄前，特殊欄續存於此）。

    Args:
        canon: normalize_row 產出（含 source / content / score / prod_oid …）。
        raw: 原始一列（保留全欄）。

    Returns:
        InboundItem。
    """
    source = canon.get("source", "manual")
    srid = str(canon.get("source_record_id") or "").strip()
    content = str(canon.get("content") or "").strip()
    item_id = f"{source}-{_dedup_hash(srid or content)}" if (srid or content) else _make_id(source, "", "")
    return InboundItem(
        item_id=item_id,
        source=source,
        prod_oid=str(canon.get("prod_oid") or ""),
        pkg_oid=str(canon.get("pkg_oid") or ""),
        rating=_parse_rating(str(canon.get("score") or "")),
        comment=content,
        raw={str(k): v for k, v in raw.items()},
        status="pending",
        created_at=_now(),
        occurred_at=str(canon.get("occurred_at") or ""),  # 原始事件時間（排序用）
    )


def _dedup_hash(s: str) -> str:
    """16 碼短雜湊（item_id 去重用）。"""
    return hashlib.sha1(s.encode()).hexdigest()[:16]
