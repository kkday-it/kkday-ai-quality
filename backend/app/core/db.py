"""本地數據庫（SQLite）— 錄入標的 + 判決結果持久化。

MVP 用 SQLite（零依賴、單檔）；資料量成長或需語義去重時再換 PostgreSQL + pgvector。
DB 檔：backend/data/aiqc.db（gitignore）。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app.core.schema import InboundItem, TicketFinding

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "aiqc.db"


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """建表（冪等）。"""
    with _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS inbound_items (
                item_id    TEXT PRIMARY KEY,
                source     TEXT,
                prod_oid   TEXT,
                pkg_oid    TEXT,
                rating     INTEGER,
                comment    TEXT,
                raw        TEXT,
                status     TEXT,
                created_at TEXT
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS findings (
                finding_id TEXT PRIMARY KEY,
                item_id    TEXT,
                prod_oid   TEXT,
                dimension  TEXT,
                verdict    TEXT,
                confidence REAL,
                data       TEXT,
                status     TEXT,
                created_at TEXT
            )
            """
        )


def insert_inbound(item: InboundItem) -> None:
    """單筆寫入（冪等：item_id 重複則覆蓋）。"""
    with _conn() as c:
        c.execute(
            """
            INSERT OR REPLACE INTO inbound_items
                (item_id, source, prod_oid, pkg_oid, rating, comment, raw, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.item_id,
                item.source,
                item.prod_oid,
                item.pkg_oid,
                item.rating,
                item.comment,
                json.dumps(item.raw, ensure_ascii=False),
                item.status,
                item.created_at,
            ),
        )


def insert_inbound_batch(items: list[InboundItem]) -> int:
    """批量寫入，回傳成功筆數（冪等去重後）。"""
    for it in items:
        insert_inbound(it)
    return len(items)


def list_inbound(status: str | None = None) -> list[dict]:
    """列出錄入標的（可依 status 過濾），新到舊。"""
    with _conn() as c:
        if status:
            rows = c.execute(
                "SELECT * FROM inbound_items WHERE status = ? ORDER BY created_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM inbound_items ORDER BY created_at DESC"
            ).fetchall()
    return [dict(r) for r in rows]


def insert_finding(f: TicketFinding) -> None:
    """寫入判決結果（冪等：finding_id 重複則覆蓋）。"""
    with _conn() as c:
        c.execute(
            """
            INSERT OR REPLACE INTO findings
                (finding_id, item_id, prod_oid, dimension, verdict, confidence, data, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f.finding_id,
                f.ticket_id,
                f.prod_oid,
                f.dimension,
                f.verdict,
                f.confidence,
                f.model_dump_json(),
                f.status,
                f.created_at,
            ),
        )


def insert_findings_batch(items: list[TicketFinding]) -> int:
    for it in items:
        insert_finding(it)
    return len(items)


def list_findings(prod_oid: str | None = None) -> list[dict]:
    """列出判決結果（可依 prod_oid 過濾），新到舊。data 欄還原為完整 Finding。"""
    import json as _json

    with _conn() as c:
        if prod_oid:
            rows = c.execute(
                "SELECT * FROM findings WHERE prod_oid = ? ORDER BY created_at DESC",
                (prod_oid,),
            ).fetchall()
        else:
            rows = c.execute("SELECT * FROM findings ORDER BY created_at DESC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if d.get("data"):
            d["finding"] = _json.loads(d["data"])
        out.append(d)
    return out


def update_finding_status(finding_id: str, status: str) -> bool:
    """更新單筆 Finding 狀態（confirmed/dismissed/fixed）。回傳是否命中。"""
    with _conn() as c:
        cur = c.execute(
            "UPDATE findings SET status = ? WHERE finding_id = ?", (status, finding_id)
        )
        return cur.rowcount > 0


def aggregate_findings() -> dict:
    """dimension×verdict 熱力矩陣聚合 + KPI（出口B 用）。"""
    with _conn() as c:
        matrix = [
            dict(r)
            for r in c.execute(
                "SELECT dimension, verdict, COUNT(*) AS count "
                "FROM findings GROUP BY dimension, verdict"
            ).fetchall()
        ]
        total = c.execute("SELECT COUNT(*) FROM findings").fetchone()[0]
        content = c.execute(
            "SELECT COUNT(*) FROM findings WHERE verdict IN "
            "('real_config_issue','content_missing','content_unclear')"
        ).fetchone()[0]
        by_dim = [
            dict(r)
            for r in c.execute(
                "SELECT dimension, COUNT(*) AS count FROM findings "
                "WHERE dimension != 'non_content' GROUP BY dimension ORDER BY count DESC"
            ).fetchall()
        ]
    return {
        "matrix": matrix,
        "kpi": {
            "total": total,
            "content_issue_pct": round(content / total, 3) if total else 0.0,
            "top_dimension": by_dim[0]["dimension"] if by_dim else "",
        },
        "by_dimension": by_dim,
    }

