"""schema 真相源一致性：alembic 鏈 vs `tables.metadata`，以及手寫欄名常數 vs schema。

**存在的理由**：`docker-entrypoint.sh` 走雙軌——空庫 `create_all` + stamp、既有庫 `alembic
upgrade head`。兩條路徑各自產生 schema，天生有漂移風險，而現有護欄
（`test_all_tables_have_create_migration.py`）只做**字面掃描**：它 grep migration 檔裡有沒有出現
表名字串，抓得到「完全沒寫 migration」，抓不到「寫了但與 `tables.py` 不一致」。更糟的是它的
`_KNOWN_DYNAMIC_LINEAGE` 白名單引用的兩支檔案已隨 2026-07-23 squash 被刪除，使 7 張表實質
無條件豁免。

本檔改以**真的建兩個庫、跑完各自路徑、再逐欄逐索引比對**取代字面掃描。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.core.db import tables as T
from tests.conftest import TEST_DATABASE_URL, _ensure_database

_BACKEND_ROOT = Path(__file__).resolve().parents[1]

_COLUMNS_SQL = """
SELECT table_name, column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name <> 'alembic_version'
ORDER BY 1, 2
"""

# 欄位**順序**單獨比一次：上面那條 ORDER BY 欄名，天生看不到 ordinal_position 差異。
# 實際踩過——`ADD COLUMN` 一律把欄位加在最後，於是「跑 migration 鏈的庫」與「create_all 的新庫」
# 欄序不同（attribution_oid 在前者是第 21 欄、後者是第 1 欄），而原本的比對完全沒紅。
# 註解也要比：本輪的主要交付物就是 13 表 + 126 欄的 COMMENT，而原本的比對 SQL 結構上
# 看不到它——實測 tables.py 與 DB 曾漂移 2 欄（migration 與宣告寫了不同文字），沒有任何紅燈。
_COMMENT_SQL = """
SELECT c.relname, a.attname, d.description
  FROM pg_description d
  JOIN pg_class c ON c.oid = d.objoid
  JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = d.objsubid
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'public' AND d.objsubid > 0
 ORDER BY 1, 2
"""

_COLUMN_ORDER_SQL = """
SELECT table_name, ordinal_position, column_name
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name <> 'alembic_version'
ORDER BY 1, 2
"""

_INDEXES_SQL = """
SELECT tablename, indexname, indexdef FROM pg_indexes
WHERE schemaname = 'public' AND tablename <> 'alembic_version'
ORDER BY 1, 2
"""

_CONSTRAINTS_SQL = """
SELECT table_name, constraint_type, constraint_name
FROM information_schema.table_constraints
WHERE table_schema = 'public' AND table_name <> 'alembic_version'
  AND constraint_type IN ('PRIMARY KEY', 'UNIQUE')
ORDER BY 1, 2, 3
"""


def _drop_and_create(url: str) -> None:
    """把 scratch 庫砍掉重建（確保是真正的空庫，不受前次殘留影響）。"""
    u = make_url(url)
    admin = create_engine(u.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as c:
            # WITH (FORCE)（PG13+）＝踢連線與 DROP 同一個原子動作；分兩步（先 pg_terminate_backend
            # 再 DROP）中間有窗口讓新連線擠進來，DROP 就會以 "is being accessed by other users" 失敗。
            c.execute(text(f'DROP DATABASE IF EXISTS "{u.database}" WITH (FORCE)'))
            c.execute(text(f'CREATE DATABASE "{u.database}"'))
    finally:
        admin.dispose()


def _drop(url: str) -> None:
    """收尾刪除 scratch 庫（失敗不影響測試結果，僅為不留垃圾）。"""
    u = make_url(url)
    admin = create_engine(u.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{u.database}" WITH (FORCE)'))
    except Exception:  # noqa: BLE001  清理失敗不該讓測試變紅
        pass
    finally:
        admin.dispose()


def _scratch_url(suffix: str) -> str:
    """以測試庫 URL 為模板，換一個獨立 dbname（與 temp_db 隔離，互不干擾）。

    dbname 帶 pid：多個 pytest 行程同時跑（多 agent / CI 並行）時，共用同一個固定 dbname 會在
    DROP↔CREATE 之間互相插隊，症狀是 `duplicate key ... pg_database_datname_index`。
    """
    u = make_url(TEST_DATABASE_URL)
    return str(u.set(database=f"{u.database}_{suffix}_{os.getpid()}"))


@pytest.fixture
def scratch_dbs():
    """兩個全新空庫：a 走 alembic 鏈、b 走 metadata.create_all；用完刪除。"""
    url_a, url_b = _scratch_url("chain_a"), _scratch_url("chain_b")
    _ensure_database(TEST_DATABASE_URL)  # 確保 maintenance 連線可用
    _drop_and_create(url_a)
    _drop_and_create(url_b)
    try:
        yield url_a, url_b
    finally:
        _drop(url_a)
        _drop(url_b)


def _run_alembic_upgrade_head(url: str) -> None:
    """以子行程跑 `alembic upgrade head`（DATABASE_URL 指向 scratch 庫）。

    刻意走子行程而非 in-process：alembic `env.py` 取的是 `tables.get_engine()` 的模組級 engine，
    in-process 改 URL 會汙染同一輪其他測試；子行程也更貼近實際部署路徑（entrypoint 就是這樣跑的）。

    Raises:
        RuntimeError: migration 鏈執行失敗（訊息含 alembic 的 stderr 尾段）。
    """
    env = {**os.environ, "DATABASE_URL": url}
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=_BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-6:]
        raise RuntimeError("alembic upgrade head 失敗：\n" + "\n".join(tail))


def _snapshot(url: str) -> dict[str, list[tuple]]:
    """讀該庫的結構快照（欄位 / 索引 / 主鍵與唯一鍵）。"""
    eng = create_engine(url)
    try:
        with eng.connect() as c:
            return {
                "columns": [tuple(r) for r in c.execute(text(_COLUMNS_SQL))],
                "column_order": [tuple(r) for r in c.execute(text(_COLUMN_ORDER_SQL))],
                "comments": [tuple(r) for r in c.execute(text(_COMMENT_SQL))],
                "indexes": [tuple(r) for r in c.execute(text(_INDEXES_SQL))],
                "constraints": [tuple(r) for r in c.execute(text(_CONSTRAINTS_SQL))],
            }
    finally:
        eng.dispose()


def test_alembic_chain_matches_metadata(scratch_dbs):
    """空庫跑完 alembic 鏈的結構，必須與 `metadata.create_all` 逐欄逐索引相同。

    本測試曾長期為 `xfail(strict=True)`：舊 baseline `4ac23d6d20b4` 走 `metadata.create_all()`，
    產出隨 `tables.py` 漂移，表改名為 `reviews` 之後，其後引用 `product_reviews` 的 migration
    全部斷鏈（fresh DB 實測撞 `UndefinedTable`），而 `docker-entrypoint.sh` 的雙軌分流讓空庫
    從不跑鏈，因此沒人發現。`94e60400715b` 改為凍結式顯式 DDL 後修復，標記已移除。
    """
    url_a, url_b = scratch_dbs

    _run_alembic_upgrade_head(url_a)

    eng_b = create_engine(url_b)
    try:
        T.metadata.create_all(eng_b)
    finally:
        eng_b.dispose()

    snap_a, snap_b = _snapshot(url_a), _snapshot(url_b)
    for label in ("columns", "column_order", "comments", "indexes", "constraints"):
        only_alembic = sorted(set(snap_a[label]) - set(snap_b[label]))
        only_metadata = sorted(set(snap_b[label]) - set(snap_a[label]))
        assert not (only_alembic or only_metadata), (
            f"{label} 漂移 —— 只在 alembic 鏈：{only_alembic}；只在 tables.py：{only_metadata}"
        )


def test_evidence_snapshot_column_constants_cover_whole_table():
    """`qc_evidence` 的欄名常數必須完整涵蓋 evidence_snapshot（PK + 內容欄 + 快取中繼欄）。

    這幾份常數已改為從 schema 派生，本測試鎖住「派生規則本身沒有遺漏任何欄」——例如日後有人
    新增一個中繼欄卻沒加進 `_CACHE_META_COLUMNS`，它會被誤當內容欄寫進快取回傳值。
    """
    from app.core.db.qc_evidence import (
        _CACHE_META_COLUMNS,
        _NON_PAYLOAD_COLUMNS,
        _snapshot_all_columns,
    )

    pk_cols = {c.key for c in T.evidence_snapshot.primary_key.columns}
    covered = set(_snapshot_all_columns()) | pk_cols | set(_NON_PAYLOAD_COLUMNS)
    all_cols = {c.key for c in T.evidence_snapshot.columns}
    assert covered == all_cols, (
        f"欄名常數與 evidence_snapshot 不一致 —— 未涵蓋：{sorted(all_cols - covered)}；"
        f"多出（表已無此欄）：{sorted(covered - all_cols)}"
    )
    assert set(_CACHE_META_COLUMNS).issubset(all_cols), "快取中繼欄名在表中不存在"
