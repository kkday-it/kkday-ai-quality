"""AI 質檢本地表建置與灌數（schema 見 backend/sql/schema.sql）。

- 主檔  products / packages / suppliers
- 事實  orders / inquiries
- 質檢  judgments（明細，由 app/core/db.py 建/寫）+ prod_quality / pkg_quality（物化彙總）
- 錄入  intake_items / batches（由 app/core/db.py 建/寫）

來源現況：
- inquiries/orders/suppliers/products(name,bd_tag) ← 售後進線 CSV
- products 內容欄(summary/feature/desc/schedules/notice) + packages ← Sheet/dw_kkdb_product（load_product_content）
- prod_quality/pkg_quality ← judgments 聚合

執行：cd backend && .venv/bin/python -m app.core.roster
     # 指定進線 CSV：... -m app.core.roster fixtures/intake/postsale_intake_sample.csv
     # 加灌商品內容：... -m app.core.roster <intake_csv> <product_content_csv>
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.core.db import DB_PATH, _conn, init_db

_SQL_FILE = Path(__file__).resolve().parents[2] / "sql" / "schema.sql"
_DEFAULT_CSV = (
    Path(__file__).resolve().parents[2] / "fixtures" / "intake" / "postsale_intake_sample.csv"
)

# schema.py Dimension（judgments.dimension 實際值）→ 質檢彙總欄位前綴
DIM_CODE: dict[str, str] = {
    "商品定位": "positioning",
    "行程流程": "itinerary",
    "費用資訊": "fee",
    "集合資訊": "meetup",
    "使用兌換": "redeem",
    "成團條件": "group_form",
    "限制與風險": "restriction",
    "承諾與SLA": "sla",
}

# 主判定嚴重度（取最嚴重者代表該面向）
_VERDICT_SEVERITY: dict[str, int] = {
    "contract_breach": 6,
    "real_config_issue": 5,
    "content_missing": 4,
    "content_unclear": 3,
    "customer_misread": 2,
    "escalate_ops": 1,
}
_ACTIONABLE = ("real_config_issue", "content_missing", "content_unclear")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _to_float(s: str) -> float:
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


# 既有 DB 補欄（schema.sql 的 CREATE IF NOT EXISTS 不會 ALTER 舊表）→ 冪等 ADD COLUMN
_MIGRATIONS: list[tuple[str, str, str]] = [
    ("inquiries", "pkg_oid", "TEXT"),
    ("inquiries", "master_lang", "TEXT"),
    ("packages", "pkg_fee", "TEXT"),
    ("packages", "pkg_meetup", "TEXT"),
    ("packages", "pkg_refund", "TEXT"),
    ("packages", "pkg_order_process", "TEXT"),
]


def _migrate() -> None:
    """對既有 DB 補上新欄位（duplicate column 視為已遷移，忽略）。"""
    import sqlite3

    with _conn() as c:
        for table, col, typ in _MIGRATIONS:
            try:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise
        # 依賴新欄位的 index（補欄後才能建）
        c.execute("CREATE INDEX IF NOT EXISTS idx_inquiries_pkg ON inquiries(pkg_oid)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_inquiries_supplier ON inquiries(supplier_oid)")


def init_schema() -> None:
    """建全部表（db.py 的 judgments/intake_items/batches + schema.sql 的主檔/事實/彙總）。冪等。"""
    init_db()
    with _conn() as c:
        c.executescript(_SQL_FILE.read_text(encoding="utf-8"))
    _migrate()


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

    with _conn() as c:
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
                """
                INSERT OR REPLACE INTO inquiries (
                    session_oid, channel, order_oid, prod_oid, supplier_oid,
                    zendesk_ticket_id, session_create_date, sessionable_type,
                    sessionable_id, session_direction, msg_handler,
                    aggregated_messages, batch_id, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    r.get("session_oid", ""),
                    channel,
                    order_oid,
                    prod_oid,
                    supplier_oid,
                    r.get("zendesk_ticket_id", ""),
                    r.get("session_create_date", ""),
                    r.get("sessionable_type", ""),
                    r.get("sessionable_id", ""),
                    r.get("session_direction", ""),
                    r.get("msg_handler", ""),
                    r.get("aggregated_messages", ""),
                    batch_id,
                    now,
                ),
            )

        # products：只補 name/bd_tag，保留既有內容欄（Sheet 灌的）不覆蓋
        for prod_oid, p in products.items():
            c.execute(
                """
                INSERT INTO products (prod_oid, prod_name, bd_tag_note, updated_at)
                VALUES (?,?,?,?)
                ON CONFLICT(prod_oid) DO UPDATE SET
                    prod_name=COALESCE(NULLIF(excluded.prod_name,''), products.prod_name),
                    bd_tag_note=COALESCE(NULLIF(excluded.bd_tag_note,''), products.bd_tag_note),
                    updated_at=excluded.updated_at
                """,
                (prod_oid, p["prod_name"], p["bd_tag_note"], now),
            )
        for order_oid, o in orders.items():
            c.execute(
                "INSERT OR REPLACE INTO orders (order_oid, order_mid, prod_oid, order_profit, updated_at) "
                "VALUES (?,?,?,?,?)",
                (order_oid, o["order_mid"], o["prod_oid"], o["order_profit"], now),
            )
        for supplier_oid in suppliers:
            c.execute(
                "INSERT OR IGNORE INTO suppliers (supplier_oid, updated_at) VALUES (?,?)",
                (supplier_oid, now),
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

    with _conn() as c:
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
                """
                INSERT OR REPLACE INTO inquiries (
                    session_oid, channel, order_oid, prod_oid, pkg_oid, supplier_oid,
                    master_lang, zendesk_ticket_id, session_create_date, sessionable_type,
                    sessionable_id, session_direction, msg_handler,
                    aggregated_messages, batch_id, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    r.get("session_oid", ""),
                    channel,
                    order_oid,
                    prod_oid,
                    pkg_oid,
                    supplier_oid,
                    r.get("master_lang", ""),
                    r.get("zendesk_ticket_id", ""),
                    r.get("session_create_date", ""),
                    r.get("sessionable_type", ""),
                    r.get("sessionable_id", ""),
                    r.get("session_direction", ""),
                    r.get("msg_handler", ""),
                    r.get("aggregated_messages", ""),
                    batch_id,
                    now,
                ),
            )

        for prod_oid, r in products.items():
            c.execute(
                """
                INSERT INTO products (
                    prod_oid, master_lang, prod_name, prod_summary, prod_feature, prod_desc,
                    prod_schedules, prod_notice, prod_fee, prod_meetup, prod_redeem, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(prod_oid) DO UPDATE SET
                    master_lang=excluded.master_lang,
                    prod_name=COALESCE(NULLIF(excluded.prod_name,''), products.prod_name),
                    prod_summary=excluded.prod_summary, prod_feature=excluded.prod_feature,
                    prod_desc=excluded.prod_desc, prod_schedules=excluded.prod_schedules,
                    prod_notice=excluded.prod_notice, prod_fee=excluded.prod_fee,
                    prod_meetup=excluded.prod_meetup, prod_redeem=excluded.prod_redeem,
                    updated_at=excluded.updated_at
                """,
                (
                    prod_oid,
                    r.get("master_lang", ""),
                    r.get("prod_name", ""),
                    r.get("prod_summary", ""),
                    r.get("prod_feature", ""),
                    r.get("prod_desc", ""),
                    r.get("prod_schedule", ""),
                    r.get("prod_notice", ""),
                    r.get("prod_fee", ""),
                    r.get("prod_meetup", ""),
                    r.get("prod_exchange", ""),
                    now,
                ),
            )
        for order_oid, o in orders.items():
            c.execute(
                "INSERT OR REPLACE INTO orders (order_oid, order_mid, prod_oid, updated_at) VALUES (?,?,?,?)",
                (order_oid, o["order_mid"], o["prod_oid"], now),
            )
        for supplier_oid in suppliers:
            c.execute(
                "INSERT OR IGNORE INTO suppliers (supplier_oid, updated_at) VALUES (?,?)",
                (supplier_oid, now),
            )
        for pid, pk in packages.items():
            c.execute(
                """
                INSERT OR REPLACE INTO packages (
                    pkg_oid, prod_oid, pkg_name, pkg_desc, pkg_schedules,
                    pkg_fee, pkg_meetup, pkg_refund, pkg_order_process, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    pid,
                    pk.get("_prod_oid", ""),
                    pk.get("pkg_name") or "",
                    pk.get("pkg_desc") or "",
                    pk.get("pkg_schedule") or "",
                    pk.get("pkg_fee") or "",
                    pk.get("pkg_meetup") or "",
                    pk.get("pkg_refund") or "",
                    pk.get("pkg_order_process") or "",
                    now,
                ),
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
    """一組 judgment → 彙總欄位（整體 + 8 面向）。"""
    agg: dict[str, object] = {}
    for code in DIM_CODE.values():
        agg[f"{code}_n"] = 0
        agg[f"{code}_verdict"] = None
    best: dict[str, tuple[int, str]] = {}
    content_issue_n = contract_breach_n = 0
    max_conf = 0.0
    dim_counts: dict[str, int] = {}
    statuses: set[str] = set()
    last_at = ""

    for f in judgments:
        dim, verdict = f.get("dimension", ""), f.get("verdict", "")
        max_conf = max(max_conf, float(f.get("confidence") or 0.0))
        if verdict in _ACTIONABLE:
            content_issue_n += 1
        if verdict == "contract_breach":
            contract_breach_n += 1
        if f.get("status"):
            statuses.add(f["status"])
        if f.get("created_at", "") > last_at:
            last_at = f["created_at"]
        code = DIM_CODE.get(dim)
        if not code:
            continue
        agg[f"{code}_n"] = int(agg[f"{code}_n"]) + 1  # type: ignore[arg-type]
        dim_counts[dim] = dim_counts.get(dim, 0) + 1
        sev = _VERDICT_SEVERITY.get(verdict, 0)
        if code not in best or sev > best[code][0]:
            best[code] = (sev, verdict)

    for code, (_, verdict) in best.items():
        agg[f"{code}_verdict"] = verdict

    total = len(judgments)
    agg.update(
        judgments_total=total,
        content_issue_n=content_issue_n,
        content_issue_pct=round(content_issue_n / total, 3) if total else 0.0,
        contract_breach_n=contract_breach_n,
        top_dimension=max(dim_counts, key=dim_counts.get) if dim_counts else None,  # type: ignore[arg-type]
        max_confidence=round(max_conf, 3),
        overall_status="/".join(sorted(statuses)) if statuses else None,
        last_judged_at=last_at or None,
    )
    return agg


def _dim_cols() -> list[str]:
    cols: list[str] = []
    for code in DIM_CODE.values():
        cols += [f"{code}_n", f"{code}_verdict"]
    return cols


def rebuild_prod_quality() -> int:
    """從 inquiries/orders/products + judgments 重建 prod_quality。"""
    with _conn() as c:
        c.execute("DELETE FROM prod_quality")
        meta = {
            r["prod_oid"]: dict(r)
            for r in c.execute(
                """
                SELECT prod_oid,
                       COUNT(*)                  AS inquiry_count,
                       COUNT(DISTINCT order_oid) AS order_count,
                       MAX(supplier_oid)         AS supplier_oid
                FROM inquiries
                WHERE prod_oid != '' AND prod_oid IS NOT NULL
                GROUP BY prod_oid
                """
            ).fetchall()
        }
        profit = {
            r["prod_oid"]: r["s"]
            for r in c.execute(
                "SELECT prod_oid, SUM(order_profit) AS s FROM orders WHERE prod_oid != '' GROUP BY prod_oid"
            ).fetchall()
        }
        pname = {
            r["prod_oid"]: dict(r)
            for r in c.execute("SELECT prod_oid, prod_name, bd_tag_note FROM products").fetchall()
        }

        by_prod: dict[str, list[dict]] = {}
        for f in c.execute("SELECT * FROM judgments").fetchall():
            by_prod.setdefault(f["prod_oid"] or "", []).append(dict(f))

        all_prods = set(meta) | {k for k in by_prod if k} | set(pname)
        cols = (
            [
                "prod_oid",
                "prod_name",
                "bd_tag_note",
                "supplier_oid",
                "inquiry_count",
                "order_count",
                "order_profit_sum",
                "judgments_total",
                "content_issue_n",
                "content_issue_pct",
                "contract_breach_n",
                "top_dimension",
                "max_confidence",
                "overall_status",
            ]
            + _dim_cols()
            + ["last_judged_at"]
        )
        ph = ",".join("?" * len(cols))

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
            c.execute(
                f"INSERT OR REPLACE INTO prod_quality ({','.join(cols)}) VALUES ({ph})",
                [row.get(col) for col in cols],
            )
        return len(all_prods)


def rebuild_pkg_quality() -> int:
    """從 judgments（去重 pkg_oid）+ packages/products 重建 pkg_quality。"""
    with _conn() as c:
        c.execute("DELETE FROM pkg_quality")
        pkg_prod = {
            r["pkg_oid"]: r["prod_oid"]
            for r in c.execute("SELECT pkg_oid, prod_oid FROM packages").fetchall()
        }
        pname = {
            r["prod_oid"]: r["prod_name"]
            for r in c.execute("SELECT prod_oid, prod_name FROM products").fetchall()
        }

        by_pkg: dict[str, list[dict]] = {}
        for f in c.execute(
            "SELECT * FROM judgments WHERE pkg_oid != '' AND pkg_oid IS NOT NULL"
        ).fetchall():
            by_pkg.setdefault(f["pkg_oid"], []).append(dict(f))

        cols = (
            [
                "pkg_oid",
                "prod_oid",
                "prod_name",
                "inquiry_count",
                "judgments_total",
                "content_issue_n",
                "content_issue_pct",
                "contract_breach_n",
                "top_dimension",
                "max_confidence",
                "overall_status",
            ]
            + _dim_cols()
            + ["last_judged_at"]
        )
        ph = ",".join("?" * len(cols))

        for pkg_oid in sorted(by_pkg):
            prod_oid = pkg_prod.get(pkg_oid) or (by_pkg[pkg_oid][0].get("prod_oid") or "")
            row = {
                "pkg_oid": pkg_oid,
                "prod_oid": prod_oid,
                "prod_name": pname.get(prod_oid, ""),
                "inquiry_count": 0,
                **_aggregate(by_pkg[pkg_oid]),
            }
            c.execute(
                f"INSERT OR REPLACE INTO pkg_quality ({','.join(cols)}) VALUES ({ph})",
                [row.get(col) for col in cols],
            )
        return len(by_pkg)


def build_all(csv_path: str | Path | None = None, product_csv: str | Path | None = None) -> dict:
    """一鍵：建表 → 灌進線 → (選)灌商品內容 → 重建質檢彙總。"""
    init_schema()
    src = Path(csv_path) if csv_path else _DEFAULT_CSV
    stats: dict = {"db": str(DB_PATH)}
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
    供 load_product_content（CSV）與 datasource.product_refresh（方案①即時撈）共用同一 upsert，
    既有 prod_name 為空不覆蓋（COALESCE），其餘內容欄以最新值覆蓋（即時撈＝當下最新，免 stale）。
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

    with _conn() as c:
        for prod_oid, r in prods.items():
            c.execute(
                """
                INSERT INTO products (
                    prod_oid, prod_mid, master_lang, prod_name, prod_summary,
                    prod_feature, prod_desc, prod_schedules, prod_notice,
                    prod_fee, prod_meetup, prod_redeem, prod_purchase, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(prod_oid) DO UPDATE SET
                    prod_mid=excluded.prod_mid, master_lang=excluded.master_lang,
                    prod_name=COALESCE(NULLIF(excluded.prod_name,''), products.prod_name),
                    prod_summary=excluded.prod_summary, prod_feature=excluded.prod_feature,
                    prod_desc=excluded.prod_desc, prod_schedules=excluded.prod_schedules,
                    prod_notice=excluded.prod_notice, prod_fee=excluded.prod_fee,
                    prod_meetup=excluded.prod_meetup, prod_redeem=excluded.prod_redeem,
                    prod_purchase=excluded.prod_purchase, updated_at=excluded.updated_at
                """,
                (
                    prod_oid,
                    r.get("prod_mid", ""),
                    r.get("master_lang", ""),
                    r.get("prod_name", ""),
                    r.get("prod_summary", ""),
                    r.get("prod_feature", ""),
                    r.get("prod_desc", ""),
                    r.get("prod_schedules", ""),
                    r.get("prod_notice", ""),
                    r.get("prod_fee", ""),
                    r.get("prod_meetup", ""),
                    r.get("prod_redeem", ""),
                    r.get("prod_purchase", ""),
                    now,
                ),
            )
        for pkg_oid, r in pkgs.items():
            c.execute(
                "INSERT OR REPLACE INTO packages (pkg_oid, prod_oid, pkg_name, pkg_desc, pkg_schedules, updated_at) "
                "VALUES (?,?,?,?,?,?)",
                (
                    pkg_oid,
                    (r.get("prod_oid") or "").strip(),
                    r.get("pkg_name", ""),
                    r.get("pkg_desc", ""),
                    r.get("pkg_schedules", ""),
                    now,
                ),
            )
    return {"products": len(prods), "packages": len(pkgs)}


if __name__ == "__main__":
    csv_arg = sys.argv[1] if len(sys.argv) > 1 else None
    prod_arg = sys.argv[2] if len(sys.argv) > 2 else None
    result = build_all(csv_arg, prod_arg)
    print("✅ AI 質檢表建置完成")
    for k, v in result.items():
        print(f"  {k}: {v}")
