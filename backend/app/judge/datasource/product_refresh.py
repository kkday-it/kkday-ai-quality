"""方案①：當批 prod_oid 字面剪枝即時撈最新商品內容 → upsert 本地 DB。

判決來源策略（取代低頻全量快取）：對「本批進線涉及的商品」即時撈當下最新內容，
同時解決 BQ 成本（字面 IN 觸發 cluster pruning）與新鮮度（每批撈最新，免「已修誤報 / 新問題漏判」）。

編排三步（DAP 擋 scripting，故 step1→step2 字面注入在 Python 端串）：
  step1 collect_prod_oids_*  ：算出本批 distinct prod_oid（小清單）
  step2 build_product_content_sql：把字面整數清單注入 sql/product_content_by_oids.sql 的 __PROD_OIDS__
  step3 refresh_product_content  ：fixture（讀 fixtures/product_content_by_oids.json）/ live（BQ，待權限）
                                   → roster.upsert_product_content_rows upsert products + packages

詳見 Confluence 子9（2130411534）問題① 與 memory bigquery-dap-cost-constraints。
"""

from __future__ import annotations

import json
from pathlib import Path

from app.core import roster
from app.core.schema import NormalizedTicket

_SQL_TEMPLATE = Path(__file__).resolve().parents[3] / "sql" / "product_content_by_oids.sql"
_FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"
_PLACEHOLDER = "__PROD_OIDS__"


def _sanitize_oids(prod_oids: list[str] | list[int]) -> list[int]:
    """字面注入前清洗：只留純數字 prod_oid（防 SQL 注入 + 去重排序）。

    prod_oid 在 dw_kkdb 必為整數；非數字 / 空值直接丟棄。
    """
    out: set[int] = set()
    for o in prod_oids:
        s = str(o).strip()
        if s.isdigit():
            out.add(int(s))
    return sorted(out)


def build_product_content_sql(prod_oids: list[str] | list[int]) -> str:
    """step2：把字面整數清單注入抽取模板 → 可直接貼進 DAP console 跑的 SQL。

    空清單會 raise（避免退化成無 WHERE 的全掃）。
    """
    oids = _sanitize_oids(prod_oids)
    if not oids:
        raise ValueError("prod_oids 清洗後為空，拒絕產生無界查詢（會全掃 ~36GB）")
    literal = ",".join(str(o) for o in oids)
    return _SQL_TEMPLATE.read_text(encoding="utf-8").replace(_PLACEHOLDER, literal)


def collect_prod_oids_from_tickets(tickets: list[NormalizedTicket]) -> list[str]:
    """step1（來源＝記憶體 NormalizedTicket）：抽 distinct prod_oid。"""
    return _dedup([t.prod_oid for t in tickets])


def collect_prod_oids_from_db(channel: str = "", batch_id: str = "") -> list[str]:
    """step1（來源＝本地 inquiries 表）：抽 distinct prod_oid（可選 channel / batch_id 篩當批）。"""
    from app.core.db import _conn

    sql = "SELECT DISTINCT prod_oid FROM inquiries WHERE prod_oid != '' AND prod_oid IS NOT NULL"
    params: list[str] = []
    if channel:
        sql += " AND channel = ?"
        params.append(channel)
    if batch_id:
        sql += " AND batch_id = ?"
        params.append(batch_id)
    with _conn() as c:
        rows = c.execute(sql, params).fetchall()
    return _dedup([r["prod_oid"] for r in rows])


def refresh_product_content(prod_oids: list[str] | list[int], source: str = "fixture") -> dict:
    """step3：撈本批商品最新內容並 upsert 本地 DB。回傳編排統計。

    source=fixture（MVP，零 BQ 權限，讀 fixtures/product_content_by_oids.json）
         | live（production，BQ 跑字面剪枝 SQL，待權限）。
    """
    oids = _sanitize_oids(prod_oids)
    if not oids:
        return {"prod_oids": [], "fetched_rows": 0, "products": 0, "packages": 0, "source": source}

    rows = _from_live(oids) if source == "live" else _from_fixture(oids)
    stats = roster.upsert_product_content_rows(rows)
    return {
        "prod_oids": oids,
        "fetched_rows": len(rows),
        "products": stats["products"],
        "packages": stats["packages"],
        "source": source,
    }


def refresh_for_tickets(source_tickets: str = "fixture", source_content: str = "fixture") -> dict:
    """便利編排：售前售後進線 → 取 prod_oid → 即時撈商品內容 upsert。

    判決前的「保新鮮」前置步驟：跑完後 product.fetch_product(source='db') 讀到的即當下最新。
    """
    from app.judge.ingest.conversations import fetch_conversations

    tickets = fetch_conversations(source=source_tickets)
    oids = collect_prod_oids_from_tickets(tickets)
    return refresh_product_content(oids, source=source_content)


def _dedup(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        s = str(v or "").strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _from_fixture(oids: list[int]) -> list[dict]:
    """fixture：讀 product_content_by_oids.json（list[row]），過濾出本批 prod_oid。"""
    fp = _FIXTURES / "product_content_by_oids.json"
    if not fp.exists():
        return []
    data = json.loads(fp.read_text(encoding="utf-8"))
    want = set(oids)
    return [
        r
        for r in data.get("rows", [])
        if str(r.get("prod_oid", "")).strip().isdigit() and int(r["prod_oid"]) in want
    ]


def _from_live(oids: list[int]) -> list[dict]:
    """production：BQ 跑字面剪枝 SQL → list[row dict]（欄位同 upsert_product_content_rows 預期）。

    待 BQ 讀取權限（負責 Gary，進度待確認）。需 google-cloud-bigquery（pyproject 選配）。
    """
    sql = build_product_content_sql(oids)
    try:
        from google.cloud import bigquery  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "live 模式需 google-cloud-bigquery，且需 DAP 讀取權限；現用 source='fixture'。\n"
            "已產生可手動貼進 DAP console 的字面剪枝 SQL（見 build_product_content_sql）。"
        ) from e
    from app.core.config import env

    client = bigquery.Client(project=env.bigquery_project_id)
    return [dict(row) for row in client.query(sql).result()]


if __name__ == "__main__":
    import sys

    src = sys.argv[1] if len(sys.argv) > 1 else "fixture"  # fixture | live

    # step1：優先從本地 inquiries 取當批 prod_oid；DB 空則退回 conversations 進線 fixture
    oids = collect_prod_oids_from_db()
    origin = "inquiries 表"
    if not oids:
        from app.judge.ingest.conversations import fetch_conversations

        oids = collect_prod_oids_from_tickets(fetch_conversations(source="fixture"))
        origin = "conversations fixture"
    clean = _sanitize_oids(oids)
    sample = clean[:8] + (["…"] if len(clean) > 8 else [])

    print(f"step1 ▸ 本批 prod_oid（來源：{origin}）：{len(clean)} 個 {sample}")
    if clean:
        sql = build_product_content_sql(clean)
        where_line = next(
            (
                ln.strip()
                for ln in sql.splitlines()
                if not ln.strip().startswith("--") and "prod_oid IN (" in ln
            ),
            "",
        )
        preview = where_line if len(where_line) <= 90 else where_line[:90] + " …)"
        print(f"step2 ▸ 字面剪枝 WHERE：{preview}")
        stats = refresh_product_content(clean, source=src)
        print(
            f"step3 ▸ upsert（source={src}）：fetched_rows={stats['fetched_rows']} "
            f"products={stats['products']} packages={stats['packages']}"
        )
    else:
        print("（無 prod_oid，略過）")
