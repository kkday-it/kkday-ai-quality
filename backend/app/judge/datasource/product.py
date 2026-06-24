"""商品欄位：fetch_product + extract_fields → ProductConfig（9 邏輯欄位）。

MVP 走 fixture；production 走 api-b2c CDN（正規憑證，沿用 general_kkday_extractor 映射邏輯）。
"""

from __future__ import annotations

import json
from pathlib import Path

from app.core.schema import ProductConfig

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"

LOGICAL_FIELDS = [
    "prod_name",
    "prod_summary",
    "prod_feature",
    "prod_schedules",
    "prod_notice",
    "prod_fee",       # 費用資訊 PMDL_INC_NINC
    "prod_meetup",    # 集合資訊 PMDL_VENUE_LOCATION
    "prod_redeem",    # 使用兌換 PMDL_EXCHANGE_LOCATION
    "prod_purchase",  # 購買須知 PMDL_PURCHASE_SUMMARY
    "pkg_desc",
    "pkg_schedules",
]


def fetch_product(prod_id: str, source: str = "fixture") -> dict:
    """取商品原始 JSON。source=fixture（MVP）| db（本地 products/packages 表）| live（api-b2c）。"""
    if source == "live":
        return _from_live(prod_id)
    if source == "db":
        return _from_db(prod_id)
    fp = FIXTURES / f"product_{prod_id}.json"
    return json.loads(fp.read_text(encoding="utf-8")) if fp.exists() else {}


def _from_db(prod_id: str) -> dict:
    """本地 DB：products/packages 表 → extract_fields 可吃的 {product:{fields:{邏輯欄位}}}。"""
    from app.core.db import _conn

    with _conn() as c:
        p = c.execute(
            "SELECT prod_name, prod_summary, prod_feature, prod_schedules, prod_notice, "
            "prod_fee, prod_meetup, prod_redeem, prod_purchase "
            "FROM products WHERE prod_oid = ?",
            (str(prod_id),),
        ).fetchone()
        pk = c.execute(
            "SELECT pkg_desc, pkg_schedules FROM packages WHERE prod_oid = ? LIMIT 1",
            (str(prod_id),),
        ).fetchone()
    if not p:
        return {}
    fields = {
        k: (p[k] or "")
        for k in (
            "prod_name", "prod_summary", "prod_feature", "prod_schedules", "prod_notice",
            "prod_fee", "prod_meetup", "prod_redeem", "prod_purchase",
        )
    }
    if pk:
        fields["pkg_desc"] = pk["pkg_desc"] or ""
        fields["pkg_schedules"] = pk["pkg_schedules"] or ""
    return {"product": {"fields": fields}}


def extract_fields(prod_id: str, product_json: dict) -> ProductConfig:
    """商品 JSON → 9 邏輯欄位（容錯 fixture 與 live 兩種結構）。"""
    fields: dict[str, str] = {}
    p = product_json.get("product", {})
    src = p.get("fields", {}) if isinstance(p.get("fields"), dict) else {}

    # fixture 直給的邏輯欄位
    for k in LOGICAL_FIELDS:
        if src.get(k):
            fields[k] = str(src[k])
    # 別名/補位
    if not fields.get("prod_name") and p.get("prod_name"):
        fields["prod_name"] = str(p["prod_name"])
    if not fields.get("prod_notice") and src.get("prod_notice_excerpt"):
        fields["prod_notice"] = str(src["prod_notice_excerpt"])
    if not fields.get("pkg_desc") and p.get("selected_pkg_name"):
        fields["pkg_desc"] = str(p["selected_pkg_name"])

    return ProductConfig(prod_oid=str(prod_id), fields=fields)


def _from_live(prod_id: str) -> dict:
    """production：api-b2c CDN（正規憑證；header/token 由環境變數注入，不寫死、不用 verify=False）。"""
    import os

    import httpx

    base = "https://api-b2c.kkday.com/api/v2/cdn/product"
    headers = {
        "b2c-token1": os.environ.get("KKDAY_B2C_TOKEN1", ""),
        "x-auth-token": os.environ.get("KKDAY_X_AUTH_TOKEN", ""),
        "lang": "zh-tw",
        "locale": "tw",
        "currency": "TWD",
    }
    with httpx.Client(timeout=30) as c:  # 正規 TLS 驗證（預設 verify=True）
        resp = c.get(f"{base}/{prod_id}", headers=headers)
        resp.raise_for_status()
        return resp.json()
