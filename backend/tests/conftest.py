"""pytest 共用 fixture：初判測試走 stub 模式（零 key）+ 隔離 PostgreSQL 測試庫。

DB 為 PostgreSQL only，測試一律指向專用測試庫 `kkdb_ai_quality_test`（與 dev 庫隔離），
測試庫不存在會自動建立（免手動 createdb）。覆寫測試庫 URL：env TEST_DATABASE_URL。

engine 於**本模組載入時**（早於任何 test module import）就定錨到測試庫且全程不還原，
並由 `engine_connect` 硬守衛把「連到非測試庫」變成立刻紅燈——理由見下方兩段註解。
"""

from __future__ import annotations

import os
import threading
import time
import traceback

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine, make_url

from app.core import db
from app.core.config import env
from app.core.db import tables as T

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg2://localhost:5432/kkdb_ai_quality_test",
)

_TEST_DB_NAME = make_url(TEST_DATABASE_URL).database
_MAINTENANCE_DB = "postgres"  # CREATE/DROP DATABASE 必須連的維護庫


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


# ══ 測試期資料庫硬守衛 ════════════════════════════════════════════════════════
# 舊寫法是「temp_db 換全域 engine → 測試結束把 T._engine 還原成 dev engine」。背景執行緒
# （prejudge / export 的 daemon thread、其 ThreadPoolExecutor worker、job sweeper）不受
# fixture 生命週期約束，還原之後才跑到落庫的那些寫入拿到的就是 **dev engine**——實測
# dev 庫 attribution_event_lst 已被塞進 474 筆 source_id='bad' 的測試垃圾列（2026-07-14 起）。
# 現在改成：engine 全程定錨測試庫（見下方 module 級 set_engine）+ 本守衛當 backstop，
# 未來任何同類逃逸都是立刻紅燈，而不是靜默污染。

_db_escapes: list[str] = []


def _is_test_db(name: str | None) -> bool:
    """該 dbname 是否為測試期允許連的庫。

    允許三類：測試庫本身、以它為前綴的 scratch 庫（`*_bootstrap` / `*_chain_a`…，由
    test_schema_bootstrap / test_schema_parity 自建自刪）、以及 maintenance 庫 `postgres`
    （建/刪 scratch 庫的唯一入口）。
    """
    if not name:
        return False
    return name == _MAINTENANCE_DB or name == _TEST_DB_NAME or name.startswith(f"{_TEST_DB_NAME}_")


@event.listens_for(Engine, "engine_connect")
def _forbid_non_test_db(conn) -> None:
    """任何指向非測試庫的連線一律當場拋錯（連到 dev 正式庫 = 缺陷，不是可容忍的雜訊）。

    同時記錄到 `_db_escapes` 再拋：呼叫端多半包在 best-effort 的 `except Exception: pass`
    裡（`update_prejudge_run_status` / `save_run_log` / usage flush 皆是），只拋錯會被吞掉、
    測試照樣全綠。靠 `_assert_no_db_escape` 讀這份紀錄把測試判紅才是真正的閘門。
    """
    name = conn.engine.url.database
    if _is_test_db(name):
        return
    stack = "".join(traceback.format_stack()[-12:-1])
    _db_escapes.append(f"→ DB `{name}`（thread={threading.current_thread().name}）\n{stack}")
    raise RuntimeError(
        f"測試期禁止連線非測試庫：{name}（僅允許 {_TEST_DB_NAME} 及其 scratch 衍生庫）"
    )


# engine 定錨：module import 時就換掉，早於任何 test module 被 import。
# 必要性——`test_docs_gate` 只是 `from app.api.main import ...`，main.py 的 module 級
# `db.seed_rules_from_files()` 就會在 collection 階段連庫；那時若還沒換 engine 就直接打 dev。
# 一併改寫 env.database_url：`resolve_url()` 是 get_engine 重建 engine 時的唯一來源，
# 不改的話任何把 `T._engine` 清成 None 的路徑都會悄悄回到 dev。
_ensure_database(TEST_DATABASE_URL)
env.database_url = TEST_DATABASE_URL
T.set_engine(TEST_DATABASE_URL)


def _drain_background_jobs(timeout: float = 10.0) -> None:
    """收乾 prejudge / export 的背景 daemon thread（先 cancel 再 join）。

    這些 thread 建立後沒有被任何地方持有 reference，只能靠 thread name
    （`prejudge-<job_id>` / `export-<job_id>`）反查 job_id 走公開 cancel API。
    不收乾的話，它們會在下一個測試清空全表之後才落庫，製造跨測試污染——
    也就是「同一套 pytest 連跑兩次結果不同」的來源。
    """
    from app.core import export_jobs
    from app.judge import prejudge_batch

    pending: list[threading.Thread] = []
    cancels = (
        ("prejudge-", prejudge_batch.cancel_job),
        ("export-", export_jobs.cancel_export),
    )
    for t in threading.enumerate():
        for prefix, cancel in cancels:
            if not t.name.startswith(prefix):
                continue
            try:
                cancel(t.name[len(prefix) :])
            except Exception:  # noqa: BLE001  清理是 best-effort，不該讓收尾本身把測試弄紅
                pass
            pending.append(t)
    deadline = time.monotonic() + timeout
    for t in pending:
        t.join(max(0.0, deadline - time.monotonic()))


@pytest.fixture(autouse=True)
def _assert_no_db_escape():
    """把「測試期連到非測試庫」判成該測試失敗（autouse：最先建立故最後拆除，涵蓋 temp_db 收尾）。"""
    yield
    if not _db_escapes:
        return
    detail = "\n".join(_db_escapes)
    count = len(_db_escapes)
    _db_escapes.clear()  # 清掉避免後續每個測試都被同一筆連坐判紅
    pytest.fail(f"偵測到 {count} 次逃逸至非測試庫的 DB 連線：\n{detail}", pytrace=False)


@pytest.fixture
def temp_db():
    """engine 指向測試庫、建表、清空全表（隔離）。

    刻意**不**存檔 / 還原 `T._engine`：還原正是背景執行緒把測試資料寫進 dev 庫的根因
    （見本檔「測試期資料庫硬守衛」段）。engine 已於 module 載入時定錨，此處重新 set 只為
    覆蓋 scratch 類 fixture（test_schema_bootstrap 的 `scratch_db`）留下的指向。
    """
    T.set_engine(TEST_DATABASE_URL)
    db.init_db()
    _drain_background_jobs()  # 先收乾上一個測試的殘留 job，否則它會寫進剛清空的表
    with T.get_engine().begin() as c:
        for tbl in reversed(T.metadata.sorted_tables):
            c.execute(tbl.delete())
    yield
    _drain_background_jobs()


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
