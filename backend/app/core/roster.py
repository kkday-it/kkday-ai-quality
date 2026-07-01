"""AI 質檢主檔 / 事實 / 質檢彙總建置與灌數（SQLAlchemy Core · PostgreSQL）。

- 主檔  products / packages / suppliers
- 事實  orders / inquiries
- 質檢  judgments（明細，app/core/db.py 寫）+ prod_quality / pkg_quality（物化彙總）
- 錄入  intake_items / batches（app/core/db.py 寫）

來源現況：
- inquiries/orders/suppliers/products(name,bd_tag) ← 售後進線 CSV
- products 內容欄 + packages ← Sheet/dw_kkdb_product（load_product_content）
- prod_quality/pkg_quality ← judgments 聚合

表結構皆定義於 app/core/tables.py（取代舊 sql/schema.sql）；建表 / 演進交 db.init_db + Alembic。

執行：cd backend && .venv/bin/python -m app.core.roster
     # 指定進線 CSV：... -m app.core.roster fixtures/intake/postsale_intake_sample.csv
     # 加灌商品內容：... -m app.core.roster <intake_csv> <product_content_csv>
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

from sqlalchemy import func, select

from app.core import db, schema
from app.core import tables as T
from app.core.utils import now_iso as _now

_DEFAULT_CSV = (
    Path(__file__).resolve().parents[2] / "fixtures" / "intake" / "postsale_intake_sample.csv"
)

# schema.py Dimension（judgments.dimension 實際值）→ 質檢彙總欄位前綴。
# key 由 schema.CONTENT_DIMENSIONS 逐位對映（不再手打中文 label，杜絕與 schema/prejudge 副本漂移）；
# value（欄位前綴）為 roster 專有、無他處副本，順序須對齊 CONTENT_DIMENSIONS。
_DIM_PREFIXES: tuple[str, ...] = (
    "positioning",
    "itinerary",
    "fee",
    "meetup",
    "redeem",
    "group_form",
    "restriction",
    "sla",
)
DIM_CODE: dict[str, str] = dict(zip(schema.CONTENT_DIMENSIONS, _DIM_PREFIXES, strict=True))

def _to_float(s: str) -> float:
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def init_schema() -> None:
    """建全部 13 表（db.py 6 + roster 7，皆在 tables.metadata）。冪等。"""
    db.init_db()


# ─────────────────────────── 來源層（CSV 灌數）───────────────────────────


def load_intake(csv_path: str | Path, channel: str = "postsale", batch_id: str = "") -> dict:
    """從售後進線 CSV 拆灌 inquiries + orders + suppliers + products(name/bd_tag)。

    正規化：profit/mid 進 orders、name/bd_tag 進 products、supplier 進 suppliers，
    inquiries 只留 session 級欄位 + 外鍵。回傳各表灌入筆數。
    """
    rows = list(csv.DictReader(Path(csv_path).open(encoding="utf-8-sig")))
    now = _now()
    products: dict[str, dict] = {}
    orders: dict[str, dict] = {}
    suppliers: set[str] = set()

    with T.get_engine().begin() as c:
        for r in rows:
            prod_oid = r.get("prod_oid", "")
            order_oid = r.get("order_oid", "")
            supplier_oid = r.get("supplier_oid", "")

            if prod_oid:
                products[prod_oid] = {
                    "prod_name": r.get("prod_name_zh_tw", ""),
                    "bd_tag_note": r.get("prod_bd_tag_note", ""),
                }
            if order_oid:
                orders[order_oid] = {
                    "order_mid": r.get("order_mid", ""),
                    "prod_oid": prod_oid,
                    "order_profit": _to_float(r.get("order_profit", "")),
                }
            if supplier_oid:
                suppliers.add(supplier_oid)

            c.execute(
                T.upsert(
                    T.inquiries,
                    {
                        "session_oid": r.get("session_oid", ""),
                        "channel": channel,
                        "order_oid": order_oid,
                        "prod_oid": prod_oid,
                        "supplier_oid": supplier_oid,
                        "zendesk_ticket_id": r.get("zendesk_ticket_id", ""),
                        "session_create_date": r.get("session_create_date", ""),
                        "sessionable_type": r.get("sessionable_type", ""),
                        "sessionable_id": r.get("sessionable_id", ""),
                        "session_direction": r.get("session_direction", ""),
                        "msg_handler": r.get("msg_handler", ""),
                        "aggregated_messages": r.get("aggregated_messages", ""),
                        "batch_id": batch_id,
                        "created_at": now,
                    },
                    ["session_oid"],
                )
            )

        # products：只補 name/bd_tag，保留既有內容欄（Sheet 灌的）不覆蓋
        for prod_oid, p in products.items():
            c.execute(
                T.upsert_preserve(
                    T.products,
                    {
                        "prod_oid": prod_oid,
                        "prod_name": p["prod_name"],
                        "bd_tag_note": p["bd_tag_note"],
                        "updated_at": now,
                    },
                    ["prod_oid"],
                    ["prod_name", "bd_tag_note"],
                )
            )
        for order_oid, o in orders.items():
            c.execute(
                T.upsert(
                    T.orders,
                    {
                        "order_oid": order_oid,
                        "order_mid": o["order_mid"],
                        "prod_oid": o["prod_oid"],
                        "order_profit": o["order_profit"],
                        "updated_at": now,
                    },
                    ["order_oid"],
                )
            )
        for supplier_oid in suppliers:
            c.execute(
                T.upsert_ignore(
                    T.suppliers, {"supplier_oid": supplier_oid, "updated_at": now}, ["supplier_oid"]
                )
            )

    return {
        "inquiries": len(rows),
        "orders": len(orders),
        "suppliers": len(suppliers),
        "products": len(products),
    }


def load_product_content(csv_path: str | Path) -> dict:
    """從 dw_kkdb_product / Sheet 匯出的 CSV 灌 products 內容欄 + packages。

    預期表頭（Sheet `tour_product_package_all`）：
      prod_oid, prod_mid, master_lang, pkg_oid, prod_name, prod_summary, prod_feature,
      prod_desc, prod_schedules, prod_notice, pkg_name, pkg_desc, pkg_schedules
    一列＝一組 (prod×pkg)，prod 欄位隨方案重複。回傳灌入商品/方案數。
    """
    rows = list(csv.DictReader(Path(csv_path).open(encoding="utf-8-sig")))
    return upsert_product_content_rows(rows)


# merged 單檔 CSV（BigQuery intake_merged_extract.sql 輸出 24 欄）的判定：表頭含 packages_json
MERGED_MARKER = "packages_json"


def _is_merged(headers: list[str]) -> bool:
    return MERGED_MARKER in headers


def load_merged(csv_path: str | Path, channel: str = "", batch_id: str = "") -> dict:
    """從 BigQuery merged 進線 CSV（intake_merged_extract.sql 的 24 欄）一檔拆灌全表。

    一列＝一筆進線 session，商品內容欄隨進線重複、方案在 packages_json（JSON 陣列）。
    拆灌：inquiries（+pkg_oid/master_lang）、orders、suppliers、products（內容欄映射）、packages（拆 JSON）。
    欄位映射（CSV→DB，不改判決 LogicalField 契約）：prod_schedule→prod_schedules、prod_exchange→prod_redeem。
    """
    rows = list(csv.DictReader(Path(csv_path).open(encoding="utf-8-sig")))
    now = _now()
    products: dict[str, dict] = {}  # prod_oid → 內容列（取有 prod_name 的）
    orders: dict[str, dict] = {}
    suppliers: set[str] = set()
    packages: dict[str, dict] = {}  # pkg_oid → 方案 dict（含 prod_oid）

    with T.get_engine().begin() as c:
        for r in rows:
            prod_oid = (r.get("prod_oid") or "").strip()
            pkg_oid = (r.get("pkg_oid") or "").strip()
            order_oid = (r.get("order_oid") or "").strip()
            supplier_oid = (r.get("supplier_oid") or "").strip()

            # 商品內容：同一 prod_oid 取「有內容」那列（chatbot 列有、order_message 列多為空）
            if prod_oid and (prod_oid not in products or (r.get("prod_name") or "").strip()):
                products[prod_oid] = r
            if order_oid:
                orders[order_oid] = {"order_mid": r.get("order_mid", ""), "prod_oid": prod_oid}
            if supplier_oid:
                suppliers.add(supplier_oid)

            # 方案：拆 packages_json（JSON 陣列），以 pkg_oid 去重
            raw_pkgs = (r.get("packages_json") or "").strip()
            if raw_pkgs:
                try:
                    for pk in json.loads(raw_pkgs):
                        pid = str(pk.get("pkg_oid") or "").strip()
                        if pid and pid not in packages:
                            pk["_prod_oid"] = prod_oid
                            packages[pid] = pk
                except (json.JSONDecodeError, TypeError):
                    pass

            c.execute(
                T.upsert(
                    T.inquiries,
                    {
                        "session_oid": r.get("session_oid", ""),
                        "channel": channel,
                        "order_oid": order_oid,
                        "prod_oid": prod_oid,
                        "pkg_oid": pkg_oid,
                        "supplier_oid": supplier_oid,
                        "master_lang": r.get("master_lang", ""),
                        "zendesk_ticket_id": r.get("zendesk_ticket_id", ""),
                        "session_create_date": r.get("session_create_date", ""),
                        "sessionable_type": r.get("sessionable_type", ""),
                        "sessionable_id": r.get("sessionable_id", ""),
                        "session_direction": r.get("session_direction", ""),
                        "msg_handler": r.get("msg_handler", ""),
                        "aggregated_messages": r.get("aggregated_messages", ""),
                        "batch_id": batch_id,
                        "created_at": now,
                    },
                    ["session_oid"],
                )
            )

        for prod_oid, r in products.items():
            c.execute(
                T.upsert_preserve(
                    T.products,
                    {
                        "prod_oid": prod_oid,
                        "master_lang": r.get("master_lang", ""),
                        "prod_name": r.get("prod_name", ""),
                        "prod_summary": r.get("prod_summary", ""),
                        "prod_feature": r.get("prod_feature", ""),
                        "prod_desc": r.get("prod_desc", ""),
                        "prod_schedules": r.get("prod_schedule", ""),
                        "prod_notice": r.get("prod_notice", ""),
                        "prod_fee": r.get("prod_fee", ""),
                        "prod_meetup": r.get("prod_meetup", ""),
                        "prod_redeem": r.get("prod_exchange", ""),
                        "updated_at": now,
                    },
                    ["prod_oid"],
                    ["prod_name"],
                )
            )
        for order_oid, o in orders.items():
            c.execute(
                T.upsert(
                    T.orders,
                    {
                        "order_oid": order_oid,
                        "order_mid": o["order_mid"],
                        "prod_oid": o["prod_oid"],
                        "updated_at": now,
                    },
                    ["order_oid"],
                )
            )
        for supplier_oid in suppliers:
            c.execute(
                T.upsert_ignore(
                    T.suppliers, {"supplier_oid": supplier_oid, "updated_at": now}, ["supplier_oid"]
                )
            )
        for pid, pk in packages.items():
            c.execute(
                T.upsert(
                    T.packages,
                    {
                        "pkg_oid": pid,
                        "prod_oid": pk.get("_prod_oid", ""),
                        "pkg_name": pk.get("pkg_name") or "",
                        "pkg_desc": pk.get("pkg_desc") or "",
                        "pkg_schedules": pk.get("pkg_schedule") or "",
                        "pkg_fee": pk.get("pkg_fee") or "",
                        "pkg_meetup": pk.get("pkg_meetup") or "",
                        "pkg_refund": pk.get("pkg_refund") or "",
                        "pkg_order_process": pk.get("pkg_order_process") or "",
                        "updated_at": now,
                    },
                    ["pkg_oid"],
                )
            )

    return {
        "inquiries": len(rows),
        "orders": len(orders),
        "suppliers": len(suppliers),
        "products": len(products),
        "packages": len(packages),
    }


# ─────────────────────────── 質檢彙總（讀 judgments）───────────────────────────


def _aggregate(judgments: list[dict]) -> dict:
    """一組 judgment → 彙總欄位（整體 + 8 面向）。

    註：verdict 軸已移除（單軸收斂為 polarity + L1→L3 歸因），content_issue_n /
    contract_breach_n 原依 verdict 判定，暫固定為 0（欄位保留於 prod_quality/pkg_quality，
    待新判準定義後重新計算，非本次移除範圍）。
    """
    agg: dict[str, object] = {}
    for code in DIM_CODE.values():
        agg[f"{code}_n"] = 0
    max_conf = 0.0
    dim_counts: dict[str, int] = {}
    statuses: set[str] = set()
    last_at = ""

    for f in judgments:
        dim = f.get("dimension", "")
        max_conf = max(max_conf, float(f.get("confidence") or 0.0))
        if f.get("status"):
            statuses.add(f["status"])
        if (f.get("created_at") or "") > last_at:
            last_at = f["created_at"]
        code = DIM_CODE.get(dim)
        if not code:
            continue
        agg[f"{code}_n"] = int(agg[f"{code}_n"]) + 1  # type: ignore[arg-type]
        dim_counts[dim] = dim_counts.get(dim, 0) + 1

    total = len(judgments)
    agg.update(
        judgments_total=total,
        content_issue_n=0,
        content_issue_pct=0.0,
        contract_breach_n=0,
        top_dimension=max(dim_counts, key=dim_counts.get) if dim_counts else None,  # type: ignore[arg-type]
        max_confidence=round(max_conf, 3),
        overall_status="/".join(sorted(statuses)) if statuses else None,
        last_judged_at=last_at or None,
    )
    return agg


def rebuild_prod_quality() -> int:
    """從 inquiries/orders/products + judgments 重建 prod_quality。"""
    inq, ords, prods, judg = T.inquiries, T.orders, T.products, T.judgments
    with T.get_engine().begin() as c:
        c.execute(T.prod_quality.delete())
        meta = {
            r["prod_oid"]: dict(r)
            for r in c.execute(
                select(
                    inq.c.prod_oid,
                    func.count().label("inquiry_count"),
                    func.count(inq.c.order_oid.distinct()).label("order_count"),
                    func.max(inq.c.supplier_oid).label("supplier_oid"),
                )
                .where(inq.c.prod_oid != "", inq.c.prod_oid.is_not(None))
                .group_by(inq.c.prod_oid)
            ).mappings()
        }
        profit = {
            r["prod_oid"]: r["s"]
            for r in c.execute(
                select(ords.c.prod_oid, func.sum(ords.c.order_profit).label("s"))
                .where(ords.c.prod_oid != "")
                .group_by(ords.c.prod_oid)
            ).mappings()
        }
        pname = {
            r["prod_oid"]: dict(r)
            for r in c.execute(
                select(prods.c.prod_oid, prods.c.prod_name, prods.c.bd_tag_note)
            ).mappings()
        }

        by_prod: dict[str, list[dict]] = {}
        for f in c.execute(select(judg)).mappings():
            by_prod.setdefault(f["prod_oid"] or "", []).append(dict(f))

        all_prods = set(meta) | {k for k in by_prod if k} | set(pname)
        for prod_oid in sorted(all_prods):
            m = meta.get(prod_oid, {})
            pm = pname.get(prod_oid, {})
            row = {
                "prod_oid": prod_oid,
                "prod_name": pm.get("prod_name") or "",
                "bd_tag_note": pm.get("bd_tag_note") or "",
                "supplier_oid": m.get("supplier_oid") or "",
                "inquiry_count": m.get("inquiry_count") or 0,
                "order_count": m.get("order_count") or 0,
                "order_profit_sum": round(profit.get(prod_oid) or 0.0, 4),
                **_aggregate(by_prod.get(prod_oid, [])),
            }
            c.execute(T.upsert(T.prod_quality, row, ["prod_oid"]))
        return len(all_prods)


def rebuild_pkg_quality() -> int:
    """從 judgments（去重 pkg_oid）+ packages/products 重建 pkg_quality。"""
    pkgs, prods, judg = T.packages, T.products, T.judgments
    with T.get_engine().begin() as c:
        c.execute(T.pkg_quality.delete())
        pkg_prod = {
            r["pkg_oid"]: r["prod_oid"]
            for r in c.execute(select(pkgs.c.pkg_oid, pkgs.c.prod_oid)).mappings()
        }
        pname = {
            r["prod_oid"]: r["prod_name"]
            for r in c.execute(select(prods.c.prod_oid, prods.c.prod_name)).mappings()
        }

        by_pkg: dict[str, list[dict]] = {}
        for f in c.execute(
            select(judg).where(judg.c.pkg_oid != "", judg.c.pkg_oid.is_not(None))
        ).mappings():
            by_pkg.setdefault(f["pkg_oid"], []).append(dict(f))

        for pkg_oid in sorted(by_pkg):
            prod_oid = pkg_prod.get(pkg_oid) or (by_pkg[pkg_oid][0].get("prod_oid") or "")
            row = {
                "pkg_oid": pkg_oid,
                "prod_oid": prod_oid,
                "prod_name": pname.get(prod_oid, ""),
                "inquiry_count": 0,
                **_aggregate(by_pkg[pkg_oid]),
            }
            c.execute(T.upsert(T.pkg_quality, row, ["pkg_oid"]))
        return len(by_pkg)


def build_all(csv_path: str | Path | None = None, product_csv: str | Path | None = None) -> dict:
    """一鍵：建表 → 灌進線 → (選)灌商品內容 → 重建質檢彙總。"""
    init_schema()
    src = Path(csv_path) if csv_path else _DEFAULT_CSV
    stats: dict = {"db": T.resolve_url()}
    if src.exists():
        with src.open(encoding="utf-8-sig") as fh:
            headers = next(csv.reader(fh), [])
        # merged 單檔（含 packages_json）→ 一檔拆全表；否則走舊「進線 CSV + 商品 CSV」雙檔
        if _is_merged(headers):
            stats["merged"] = load_merged(src)
        else:
            stats["intake"] = load_intake(src, channel="postsale")
    else:
        stats["intake"] = "(無 CSV)"
    if product_csv and Path(product_csv).exists():
        stats["product_content"] = load_product_content(product_csv)
    stats["prod_quality"] = rebuild_prod_quality()
    stats["pkg_quality"] = rebuild_pkg_quality()
    return stats


def upsert_product_content_rows(rows: list[dict]) -> dict:
    """商品內容列（dw_kkdb_product / Sheet 匯出 / 方案① BQ 即時撈，同構欄位）→ upsert products + packages。

    一列＝一組 (prod×pkg)，prod 欄位隨方案重複，以 prod_oid / pkg_oid 去重。
    既有 prod_name 為空不覆蓋（preserve），其餘內容欄以最新值覆蓋（即時撈＝當下最新，免 stale）。
    """
    now = _now()
    prods: dict[str, dict] = {}
    pkgs: dict[str, dict] = {}
    for r in rows:
        prod_oid = (r.get("prod_oid") or "").strip()
        pkg_oid = (r.get("pkg_oid") or "").strip()
        if prod_oid and prod_oid not in prods:
            prods[prod_oid] = r
        if pkg_oid and pkg_oid not in pkgs:
            pkgs[pkg_oid] = r

    with T.get_engine().begin() as c:
        for prod_oid, r in prods.items():
            c.execute(
                T.upsert_preserve(
                    T.products,
                    {
                        "prod_oid": prod_oid,
                        "prod_mid": r.get("prod_mid", ""),
                        "master_lang": r.get("master_lang", ""),
                        "prod_name": r.get("prod_name", ""),
                        "prod_summary": r.get("prod_summary", ""),
                        "prod_feature": r.get("prod_feature", ""),
                        "prod_desc": r.get("prod_desc", ""),
                        "prod_schedules": r.get("prod_schedules", ""),
                        "prod_notice": r.get("prod_notice", ""),
                        "prod_fee": r.get("prod_fee", ""),
                        "prod_meetup": r.get("prod_meetup", ""),
                        "prod_redeem": r.get("prod_redeem", ""),
                        "prod_purchase": r.get("prod_purchase", ""),
                        "updated_at": now,
                    },
                    ["prod_oid"],
                    ["prod_name"],
                )
            )
        for pkg_oid, r in pkgs.items():
            c.execute(
                T.upsert(
                    T.packages,
                    {
                        "pkg_oid": pkg_oid,
                        "prod_oid": (r.get("prod_oid") or "").strip(),
                        "pkg_name": r.get("pkg_name", ""),
                        "pkg_desc": r.get("pkg_desc", ""),
                        "pkg_schedules": r.get("pkg_schedules", ""),
                        "updated_at": now,
                    },
                    ["pkg_oid"],
                )
            )
    return {"products": len(prods), "packages": len(pkgs)}


if __name__ == "__main__":
    csv_arg = sys.argv[1] if len(sys.argv) > 1 else None
    prod_arg = sys.argv[2] if len(sys.argv) > 2 else None
    result = build_all(csv_arg, prod_arg)
    print("✅ AI 質檢表建置完成")
    for k, v in result.items():
        print(f"  {k}: {v}")
