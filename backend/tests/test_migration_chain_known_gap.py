"""已知缺陷斷言（xfail-strict 精神）：migration 鏈從真正全空庫無法跑到 head。

背景：鏈上第 2 支 c24e5b0964ce_roster_tables.py 對 prod_quality 表 create_index，
但沒有任何 migration 建過該表（base 只建 6 張表）——docker-entrypoint.sh /
core/db/ingest.py 的 docstring 已明文承認此設計事實（fresh 庫改走 create_all+stamp，
不走 alembic upgrade from base）。

本測試把這個已知缺陷釘成可執行斷言：不是要它變綠，是要在「行為意外變得更糟或
意外變好」時發出警訊（例如有人不小心把鏈接續好了卻沒同步移除本測試/更新文件，
或有人在中段插入新 migration 導致崩潰點/錯誤訊息改變而不自知）。

用 subprocess 執行（而非同進程呼叫 alembic.command），避免污染 pytest 進程已快取的
tables._engine / metadata 狀態，且更貼近生產 entrypoint 的實際呼叫方式
（python -m alembic upgrade head）。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_KNOWN_CRASH_REVISION = "c24e5b0964ce"
_KNOWN_CRASH_TABLE = "prod_quality"


def _admin_url() -> str:
    base = os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql+psycopg2://localhost:5432/kkdb_ai_quality_test",
    )
    return str(make_url(base).set(database="postgres"))


def _fresh_db_url(dbname: str) -> str:
    base = os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql+psycopg2://localhost:5432/kkdb_ai_quality_test",
    )
    return str(make_url(base).set(database=dbname))


def test_migration_chain_cannot_run_from_truly_empty_database() -> None:
    """真正全空庫（未經 create_all）跑 `alembic upgrade head`，應在已知 revision 崩潰。

    這是刻意設計的雙軌 schema 契約（見 backend/app/core/db/README.md「雙軌 schema 契約」節），
    不是待修的 bug；本測試存在的意義是讓這個契約「若被意外打破或意外修好」都會被看見。
    """
    dbname = "kkdb_migration_gap_probe"
    admin = create_engine(_admin_url(), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{dbname}"'))
            c.execute(text(f'CREATE DATABASE "{dbname}"'))
    finally:
        admin.dispose()

    try:
        env = dict(os.environ)
        env["DATABASE_URL"] = _fresh_db_url(dbname)
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
            cwd=str(_BACKEND_DIR),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode != 0, (
            "migration 鏈從全空庫竟然跑通了 head——若此為刻意修復（如已補齊鏈上缺表 "
            "migration），請刪除本測試並更新 backend/app/core/db/README.md 的雙軌 schema "
            "契約說明，不要留著本測試繼續假裝鏈跑不通。"
        )
        combined = result.stdout + result.stderr
        assert _KNOWN_CRASH_REVISION in combined, (
            f"預期崩潰於 revision {_KNOWN_CRASH_REVISION}，但實際輸出未提及此 revision——"
            f"崩潰點可能已改變，需重新核實鏈上現況。實際輸出：\n{combined[-2000:]}"
        )
        assert _KNOWN_CRASH_TABLE in combined, (
            f"預期錯誤訊息提及表 {_KNOWN_CRASH_TABLE}（UndefinedTable），但未命中——"
            f"實際輸出：\n{combined[-2000:]}"
        )
    finally:
        admin = create_engine(_admin_url(), isolation_level="AUTOCOMMIT")
        try:
            with admin.connect() as c:
                c.execute(text(f'DROP DATABASE IF EXISTS "{dbname}"'))
        finally:
            admin.dispose()
