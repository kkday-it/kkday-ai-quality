"""本地數據庫（SQLite）— 錄入標的 + 判決結果持久化。

MVP 用 SQLite（零依賴、單檔）；資料量成長或需語義去重時再換 PostgreSQL + pgvector。
DB 檔：backend/data/kkdb_product_quality.db（gitignore）。
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.core.schema import InboundItem, TicketFinding

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "kkdb_product_quality.db"


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_column(c: sqlite3.Connection, table: str, col: str, decl: str) -> None:
    """既有表補欄位（SQLite migration，冪等）。"""
    cols = [r[1] for r in c.execute(f"PRAGMA table_info({table})")]
    if col not in cols:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def init_db() -> None:
    """建表（冪等）。"""
    with _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS intake_items (
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
            CREATE TABLE IF NOT EXISTS judgments (
                finding_id         TEXT PRIMARY KEY,
                item_id            TEXT,
                prod_oid           TEXT,
                pkg_oid            TEXT,
                dimension          TEXT,
                verdict            TEXT,
                confidence         REAL,
                suspected_field    TEXT,
                recommended_action TEXT,
                data               TEXT,
                status             TEXT,
                created_at         TEXT
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS batches (
                batch_id       TEXT PRIMARY KEY,
                name           TEXT,
                source         TEXT,
                original_name  TEXT,
                row_count      INTEGER,
                inserted_count INTEGER,
                uploaded_at    TEXT
            )
            """
        )
        _ensure_column(c, "intake_items", "batch_id", "TEXT")
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id       TEXT PRIMARY KEY,
                email         TEXT UNIQUE,
                password_hash TEXT,
                created_at    TEXT
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id    TEXT PRIMARY KEY,
                data       TEXT,
                updated_at TEXT
            )
            """
        )


def insert_inbound(item: InboundItem) -> None:
    """單筆寫入（冪等：item_id 重複則覆蓋）。"""
    with _conn() as c:
        c.execute(
            """
            INSERT OR REPLACE INTO intake_items
                (item_id, source, batch_id, prod_oid, pkg_oid, rating, comment, raw, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.item_id,
                item.source,
                item.batch_id,
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


def list_inbound(status: str | None = None, batch_id: str | None = None) -> list[dict]:
    """列出錄入標的（可依 status / batch_id 過濾），新到舊。"""
    clauses: list[str] = []
    params: list[str] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if batch_id:
        clauses.append("batch_id = ?")
        params.append(batch_id)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with _conn() as c:
        rows = c.execute(
            f"SELECT * FROM intake_items{where} ORDER BY created_at DESC", params
        ).fetchall()
    return [dict(r) for r in rows]


def create_batch(
    source: str, source_label: str, original_name: str, row_count: int, inserted_count: int
) -> dict:
    """建立上傳批次記錄，自動命名「{來源} YYYYMMDD{當天序號:02d}」。

    例：售前售後進線 2026062301（當天該來源第 1 批）。回傳批次 dict。
    """
    now = datetime.now(timezone.utc).astimezone()
    date_iso = now.strftime("%Y-%m-%d")
    date_compact = now.strftime("%Y%m%d")
    uploaded_at = now.isoformat(timespec="seconds")
    with _conn() as c:
        seq = (
            c.execute(
                "SELECT COUNT(*) FROM batches WHERE source = ? AND substr(uploaded_at,1,10) = ?",
                (source, date_iso),
            ).fetchone()[0]
            + 1
        )
        name = f"{source_label} {date_compact}{seq:02d}"
        batch_id = f"{source}-{date_compact}-{seq:02d}"
        c.execute(
            "INSERT OR REPLACE INTO batches "
            "(batch_id, name, source, original_name, row_count, inserted_count, uploaded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (batch_id, name, source, original_name, row_count, inserted_count, uploaded_at),
        )
    return {
        "batch_id": batch_id,
        "name": name,
        "source": source,
        "original_name": original_name,
        "row_count": row_count,
        "inserted_count": inserted_count,
        "uploaded_at": uploaded_at,
    }


def list_batches() -> list[dict]:
    """列出上傳批次，新到舊。"""
    with _conn() as c:
        rows = c.execute("SELECT * FROM batches ORDER BY uploaded_at DESC").fetchall()
    return [dict(r) for r in rows]


def export_inbound_csv(batch_id: str) -> bytes:
    """把某批次明細匯出為 CSV bytes（utf-8-sig，Excel 友善）。"""
    items = list_inbound(batch_id=batch_id)
    cols = [
        "item_id",
        "source",
        "batch_id",
        "prod_oid",
        "pkg_oid",
        "rating",
        "comment",
        "status",
        "created_at",
    ]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for it in items:
        w.writerow([it.get(c, "") for c in cols])
    return buf.getvalue().encode("utf-8-sig")


def insert_finding(f: TicketFinding) -> None:
    """寫入判決結果（冪等：finding_id 重複則覆蓋）。"""
    with _conn() as c:
        c.execute(
            """
            INSERT OR REPLACE INTO judgments
                (finding_id, item_id, prod_oid, pkg_oid, dimension, verdict, confidence,
                 suspected_field, recommended_action, data, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f.finding_id,
                f.ticket_id,
                f.prod_oid,
                f.pkg_oid,
                f.dimension,
                f.verdict,
                f.confidence,
                f.suspected_field,
                f.recommended_action,
                f.model_dump_json(),
                f.status,
                f.created_at,
            ),
        )


def insert_findings_batch(items: list[TicketFinding]) -> int:
    for it in items:
        insert_finding(it)
    return len(items)


def list_findings(
    prod_oid: str | None = None,
    dimension: str | None = None,
    verdict: str | None = None,
) -> list[dict]:
    """列出判決結果（可依 prod_oid / dimension / verdict 過濾），新到舊。data 還原為完整 Finding。"""
    import json as _json

    clauses: list[str] = []
    params: list[str] = []
    for col, val in (("prod_oid", prod_oid), ("dimension", dimension), ("verdict", verdict)):
        if val:
            clauses.append(f"{col} = ?")
            params.append(val)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with _conn() as c:
        rows = c.execute(
            f"SELECT * FROM judgments{where} ORDER BY created_at DESC", params
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if d.get("data"):
            d["finding"] = _json.loads(d["data"])
        out.append(d)
    return out


def list_products() -> list[dict]:
    """有 finding 的商品清單（PM 下拉用），依問題數排序。"""
    with _conn() as c:
        rows = c.execute(
            "SELECT prod_oid, COUNT(*) AS n FROM judgments "
            "WHERE dimension != 'non_content' GROUP BY prod_oid ORDER BY n DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def update_finding_status(finding_id: str, status: str) -> bool:
    """更新單筆 Finding 狀態（confirmed/dismissed/fixed）。回傳是否命中。"""
    with _conn() as c:
        cur = c.execute(
            "UPDATE judgments SET status = ? WHERE finding_id = ?", (status, finding_id)
        )
        return cur.rowcount > 0


def aggregate_findings() -> dict:
    """dimension×verdict 熱力矩陣聚合 + KPI（出口B 用）。"""
    with _conn() as c:
        matrix = [
            dict(r)
            for r in c.execute(
                "SELECT dimension, verdict, COUNT(*) AS count "
                "FROM judgments GROUP BY dimension, verdict"
            ).fetchall()
        ]
        total = c.execute("SELECT COUNT(*) FROM judgments").fetchone()[0]
        content = c.execute(
            "SELECT COUNT(*) FROM judgments WHERE verdict IN "
            "('real_config_issue','content_missing','content_unclear')"
        ).fetchone()[0]
        by_dim = [
            dict(r)
            for r in c.execute(
                "SELECT dimension, COUNT(*) AS count FROM judgments "
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


# ── 帳號系統（users）+ per-user 設定（user_settings）─────────────────────


def create_user(user_id: str, email: str, password_hash: str) -> dict:
    """建立使用者；email 重複會拋 sqlite3.IntegrityError（呼叫端轉 409）。回傳 user dict。"""
    created_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    with _conn() as c:
        c.execute(
            "INSERT INTO users (user_id, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
            (user_id, email, password_hash, created_at),
        )
    return {"user_id": user_id, "email": email, "created_at": created_at}


def get_user_by_email(email: str) -> dict | None:
    """以 email 取使用者（含 password_hash，供登入驗證）；無則 None。"""
    with _conn() as c:
        row = c.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    return dict(row) if row else None


def get_user_by_id(user_id: str) -> dict | None:
    """以 user_id 取使用者；無則 None。"""
    with _conn() as c:
        row = c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def load_user_settings(user_id: str) -> dict | None:
    """讀某 user 的設定（完整 dict，含明文 token）；尚未存過則回 None（由上層套 _DEFAULT）。"""
    with _conn() as c:
        row = c.execute("SELECT data FROM user_settings WHERE user_id = ?", (user_id,)).fetchone()
    if not row or not row["data"]:
        return None
    try:
        return json.loads(row["data"])
    except json.JSONDecodeError:
        return None


def save_user_settings(user_id: str, data: dict) -> None:
    """覆寫某 user 的完整設定 dict（冪等：user_id 重複則覆蓋）。"""
    updated_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO user_settings (user_id, data, updated_at) VALUES (?, ?, ?)",
            (user_id, json.dumps(data, ensure_ascii=False), updated_at),
        )
