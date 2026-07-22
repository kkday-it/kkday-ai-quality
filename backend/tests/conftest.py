"""pytest 共用 fixture：初判測試走 stub 模式（零 key）+ 隔離 PostgreSQL 測試庫。

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


@pytest.fixture(autouse=True)
def _no_llm_exact_cache(monkeypatch):
    """全測試預設停用 LLM exact-cache：測試間共用相同假 prompt，開快取會互相汙染判斷
    （前測寫入→後測命中短路 _complete，assertions 全歪）且寫髒真實 data/llm_cache。
    快取行為專屬測試自行以 tmp 目錄重新開啟（見 test_llm_gateway 的 cache 組）。"""
    from app.core.config import env as _env

    monkeypatch.setattr(_env, "llm_exact_cache", False)


@pytest.fixture
def permissions_cfg(monkeypatch):
    """固定 permissions.json 內容（no_auth_grant_all=false，測 default/grants 邊界），與實檔解耦。
    供多個測試檔共用（無角色權限框架 + 需分 default/grants 兩級的端點測試）。"""
    monkeypatch.setattr(
        "app.core.permissions.local_provider._permissions_cfg",
        lambda: {
            "no_auth_grant_all": False,
            "default": [
                "finding.review.update",
                "data.source.upload",
                "data.datapack.export",
                "data.datapack.import",
                "problem.list.export",
                "prejudge.run",
            ],
            "grants": {"boss@kkday.com": ["*"]},
        },
    )


@pytest.fixture
def as_user(monkeypatch):
    """固定本地模式當前身分 email（本地模式無登入，email 僅供權限授予查詢/稽核欄位用）。"""
    from app.core.config import env

    def _set(email: str) -> None:
        monkeypatch.setattr(env, "local_user_email", email)

    return _set
