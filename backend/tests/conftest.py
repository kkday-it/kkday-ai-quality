"""pytest 共用 fixture：判決測試走 stub 模式（零 key）+ 隔離 PostgreSQL 測試庫。

DB 為 PostgreSQL only，temp_db 指向專用測試庫 `kkdb_product_quality_test`（與 dev 庫隔離），
每次 test 前清空全表確保隔離。測試需本機 PostgreSQL 在跑：
    createdb kkdb_product_quality_test
覆寫測試庫 URL：env TEST_DATABASE_URL。
"""

from __future__ import annotations

import os

import pytest

from app.core import db
from app.core import tables as T

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg2://localhost:5432/kkdb_product_quality_test",
)


@pytest.fixture
def temp_db():
    """engine 指向測試庫、建表、清空全表（隔離）；測試結束還原原 engine。"""
    saved = T._engine
    T.set_engine(TEST_DATABASE_URL)
    db.init_db()
    with T.get_engine().begin() as c:
        for tbl in reversed(T.metadata.sorted_tables):
            c.execute(tbl.delete())
    yield
    T._engine = saved
