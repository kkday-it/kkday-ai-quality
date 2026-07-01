"""product_reviews 來源專屬 ingestor：canonical row → product_reviews 實體表欄位 dict。

與 entry.item_from_canonical（intake_items 通用路徑）並存的獨立映射函式——product_reviews
拆為專表後，欄位語意（product_category/order_snap_json 等巢狀 JSON）需要專屬解析，
不再適合塞進通用 raw JSON 兜底欄，故獨立成檔（仿 entry.py 的函式結構與命名慣例）。
"""

from __future__ import annotations

import json
from typing import Any

from app.core.utils import now_iso as _now


def _parse_category(raw_value: Any) -> tuple[str | None, list[str]]:
    """解析 product_category 欄：格式 {"main": "CATEGORY_xxx", "sub": [...]}（可能是 JSON 字串或已解析 dict）。

    來源資料品質不保證（可能缺欄、非法 JSON、非 dict），防禦式解析失敗一律回 (None, [])，
    不讓單筆髒資料中斷整批匯入。

    Args:
        raw_value: 原始 product_category 欄值（str | dict | None）。

    Returns:
        (main_code, sub_codes)；解析失敗或無資料回 (None, [])。
    """
    if not raw_value:
        return None, []
    try:
        d = json.loads(raw_value) if isinstance(raw_value, str) else raw_value
        if not isinstance(d, dict):
            return None, []
        main = d.get("main")
        sub = d.get("sub") or []
        if not isinstance(sub, list):
            sub = []
        return (str(main) if main else None), [str(s) for s in sub]
    except (ValueError, TypeError):
        return None, []


def _parse_json_field(raw_value: Any) -> dict:
    """防禦式解析任意 JSON 欄位（order_snap_json 等巢狀快照）；失敗回空 dict。

    Args:
        raw_value: 原始欄值（str | dict | None）。

    Returns:
        解析後的 dict；非 dict / 解析失敗一律回 {}。
    """
    if not raw_value:
        return {}
    try:
        d = json.loads(raw_value) if isinstance(raw_value, str) else raw_value
        return d if isinstance(d, dict) else {}
    except (ValueError, TypeError):
        return {}


def row_to_product_review(canon: dict, raw: dict) -> dict:
    """source_mapping.normalize_row('product_reviews', row) 的 canonical 輸出 → product_reviews 表欄位 dict。

    item_id 以 rec_oid 決定性生成（同筆重上傳覆蓋，非內容雜湊——product_reviews 有穩定自然鍵，
    不需 entry.item_from_canonical 的雜湊兜底）。

    Args:
        canon: normalize_row 產出的 canonical dict（含 source_record_id/content/score…）。
        raw: 原始一列（保留 product_category/order_snap_json 等專屬解析來源）。

    Returns:
        可直接傳給 db.insert_product_reviews_batch 的欄位 dict（對應 tables.product_reviews 各欄）。
    """
    rec_oid = str(canon.get("source_record_id") or raw.get("rec_oid") or "").strip()
    item_id = f"product_reviews-{rec_oid}" if rec_oid else ""
    main_cat, sub_cat = _parse_category(raw.get("product_category"))
    order_snap = _parse_json_field(raw.get("order_snap_json"))
    prod_name_snapshot = order_snap if order_snap else _parse_json_field(raw.get("prod_name_snapshot"))
    return {
        "source_record_id": rec_oid or None,
        "item_id": item_id or None,
        "member_uuid": canon.get("member_uuid"),
        "traveller_type": canon.get("traveller_type") or raw.get("traveller_type"),
        "lang": canon.get("lang"),
        "occurred_at": canon.get("occurred_at"),
        "title": canon.get("title"),
        "content": canon.get("content"),
        "score": _parse_score(canon.get("score")),
        "prod_oid": canon.get("prod_oid"),
        "pkg_oid": canon.get("pkg_oid"),
        "order_oid": canon.get("order_oid"),
        "order_mid": canon.get("order_mid") or raw.get("order_mid"),
        "supplier_oid": canon.get("supplier_oid"),
        "product_category_main": main_cat,
        "product_category_sub": sub_cat,
        "go_date": canon.get("go_date") or raw.get("lst_dt_go"),
        "prod_name_snapshot": prod_name_snapshot,
        "status": "pending",
        "created_at": _now(),
        "raw": json.dumps({str(k): v for k, v in raw.items()}, ensure_ascii=False),
    }


def _parse_score(value: Any) -> int | None:
    """星等字串/數值 → int；空值或非數字回 None（防禦式，避免髒資料炸批次匯入）。"""
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return None
