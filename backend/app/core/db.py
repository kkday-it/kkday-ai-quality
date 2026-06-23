"""本地數據庫（SQLite）— 錄入標的 + 判決結果持久化。

MVP 用 SQLite（零依賴、單檔）；資料量成長或需語義去重時再換 PostgreSQL + pgvector。
DB 檔：backend/data/aiqc.db（gitignore）。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app.core.schema import InboundItem

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
