"""pytest 共用 fixture：判決測試走 stub 模式（零 key）+ 隔離 PostgreSQL 測試庫。

DB 為 PostgreSQL only，temp_db 指向專用測試庫 `kkdb_ai_quality_test`（與 dev 庫隔離），
每次 test 前建表（缺庫自動 createdb）+ 清空全表確保隔離。測試只需本機 PostgreSQL 在跑，
測試庫不存在會自動建立（免手動 createdb）。覆寫測試庫 URL：env TEST_DATABASE_URL。
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.core import db
from app.core.db import tables as T

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg2://localhost:5432/kkdb_ai_quality_test",
)


def _ensure_database(url: str) -> None:
    """測試庫不存在則自動建立（連 maintenance 庫 `postgres`，AUTOCOMMIT 執行 CREATE DATABASE）。

    CREATE DATABASE 不能在交易內執行，故用 isolation_level='AUTOCOMMIT'。dbname 來自固定
    config（非使用者輸入），以識別字引號包裹即可。
    """
    u = make_url(url)
    dbname = u.database
    admin = create_engine(u.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as c:
            exists = c.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": dbname}
            ).scalar()
            if not exists:
                c.execute(text(f'CREATE DATABASE "{dbname}"'))
    finally:
        admin.dispose()


@pytest.fixture
def temp_db():
    """engine 指向測試庫、（缺則自動建庫）建表、清空全表（隔離）；測試結束還原原 engine。"""
    saved = T._engine
    _ensure_database(TEST_DATABASE_URL)
    T.set_engine(TEST_DATABASE_URL)
    db.init_db()
    with T.get_engine().begin() as c:
        for tbl in reversed(T.metadata.sorted_tables):
            c.execute(tbl.delete())
    yield
    T._engine = saved
