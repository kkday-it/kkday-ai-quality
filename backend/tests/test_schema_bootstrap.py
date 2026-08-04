"""容器啟動時的 schema 對齊策略判定（`schema_bootstrap.resolve_startup_mode`）。

**存在的理由**：這段邏輯原本是 `docker-entrypoint.sh` 裡的 bash heredoc，唯一的驗證方式是
「重啟容器看會不會炸」——而它恰好是**最不能炸的地方**（判斷錯就是全環境 crash-loop 或
schema 靜默落後）。抽成 Python 模組後才有辦法把五種狀態逐一擺出來測。

五種狀態的實際來源：
- `fresh`     全新環境第一次部署
- `ok`        日常部署（版本認得）
- `adopt`     歷史上被 `metadata.create_all()` 建出、從未蓋章的庫
- `stamp:X`   squash 之後的既有環境（版本號還是被刪掉的那支）
- `abort:X`   降級殘留 / 人為改動 / 停在 squash 前的中間版本 → 必須擋下來
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.core.db import schema_bootstrap as sb
from app.core.db import tables as T
from tests.conftest import TEST_DATABASE_URL


@pytest.fixture
def scratch_db():
    """一個全新空庫，測完刪除；yield 期間把 tables 的 engine 指向它。"""
    u = make_url(TEST_DATABASE_URL)
    url = str(u.set(database=f"{u.database}_bootstrap"))
    target = make_url(url).database

    admin = create_engine(u.set(database="postgres"), isolation_level="AUTOCOMMIT")

    def _recreate() -> None:
        with admin.connect() as c:
            c.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :n AND pid <> pg_backend_pid()"
                ),
                {"n": target},
            )
            c.execute(text(f'DROP DATABASE IF EXISTS "{target}"'))
            c.execute(text(f'CREATE DATABASE "{target}"'))

    _recreate()
    saved = T._engine
    T.set_engine(url)
    try:
        yield url
    finally:
        T._engine = saved
        try:
            _recreate()  # 清乾淨（留一個空庫比留髒資料好）
        finally:
            admin.dispose()


def _set_version(url: str, revision: str | None) -> None:
    """在該庫建 alembic_version 並寫入（None＝建表但不寫列）。"""
    eng = create_engine(url)
    try:
        with eng.begin() as c:
            c.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS alembic_version (version_num varchar(32) NOT NULL)"
                )
            )
            c.execute(text("DELETE FROM alembic_version"))
            if revision is not None:
                c.execute(text("INSERT INTO alembic_version VALUES (:r)"), {"r": revision})
    finally:
        eng.dispose()


def test_fresh_empty_database(scratch_db):
    """全新空庫（無 alembic_version、無業務表）→ 直接 upgrade。"""
    assert sb.resolve_startup_mode() == sb.MODE_FRESH


def test_fresh_when_version_table_exists_but_empty(scratch_db):
    """有 alembic_version 表但沒有列、也沒有業務表 → 仍是全新空庫。"""
    _set_version(scratch_db, None)
    assert sb.resolve_startup_mode() == sb.MODE_FRESH


def test_adopt_when_tables_exist_without_version(scratch_db):
    """有業務表卻無版本紀錄（歷史 create_all 遺留）→ adopt。

    這條若誤判成 fresh，upgrade 會對既存表下 CREATE TABLE 直接撞 DuplicateTable。
    """
    eng = create_engine(scratch_db)
    try:
        T.metadata.create_all(eng)
    finally:
        eng.dispose()
    assert sb.resolve_startup_mode() == sb.MODE_ADOPT


def test_ok_when_revision_is_known(scratch_db):
    """版本在 script 目錄內 → 一般增量升級。"""
    from alembic.script import ScriptDirectory

    head = ScriptDirectory.from_config(sb._alembic_config()).get_current_head()
    _set_version(scratch_db, head)
    assert sb.resolve_startup_mode() == sb.MODE_OK


def test_stamp_when_revision_was_squashed_away(scratch_db):
    """版本已被 squash 刪除但登記於 SQUASHED_REVISIONS → stamp 到對照版本。

    這是本次（2026-08-04）squash 後所有既有環境會走的路徑；判斷錯就是全環境 crash-loop。
    """
    legacy, target = next(iter(sb.SQUASHED_REVISIONS.items()))
    _set_version(scratch_db, legacy)
    assert sb.resolve_startup_mode() == f"{sb.MODE_STAMP_PREFIX}{target}"


def test_abort_when_revision_is_unknown(scratch_db):
    """版本認不得且未登記 → 拒絕自動處理（自動 stamp 會謊稱最新並永久漏掉 DDL）。"""
    _set_version(scratch_db, "deadbeef1234")
    assert sb.resolve_startup_mode() == f"{sb.MODE_ABORT_PREFIX}deadbeef1234"


def test_align_schema_raises_on_abort(scratch_db):
    """abort 狀態必須拋錯讓 entrypoint 中止啟動，不能默默放行。"""
    _set_version(scratch_db, "deadbeef1234")
    with pytest.raises(RuntimeError, match="deadbeef1234"):
        sb.align_schema()


def test_align_schema_recovers_squashed_environment(scratch_db):
    """端到端：既有表 + 已被 squash 刪除的版本號 → 能自我修復到 head 且不動資料。

    ⚠️ 模擬環境**必須用 alembic 真的升到 squash 目標 revision**，不能用 `metadata.create_all()`
    ——後者建的是「當下 tables.py」的 schema（已含所有後續改名），拿去重放針對舊表名的 migration
    必然撞 UndefinedTable，那是模擬失真而非產品缺陷。用真實的鏈才能忠實重現「schema 停在該
    revision、版本號卻是已被刪除的舊值」這個待修復狀態。
    """
    from alembic.script import ScriptDirectory

    from alembic import command

    squashed_old, squash_target = next(iter(sb.SQUASHED_REVISIONS.items()))
    cfg = sb._alembic_config()
    cfg.set_main_option("sqlalchemy.url", scratch_db)
    command.upgrade(cfg, squash_target)  # schema 真的停在 squash 目標

    eng = create_engine(scratch_db)
    try:
        with eng.begin() as c:
            c.execute(text("INSERT INTO settings (key, data) VALUES ('__probe__', '{}')"))
    finally:
        eng.dispose()
    _set_version(scratch_db, squashed_old)  # 版本號改成已被 squash 刪除的舊值

    sb.align_schema()

    head = ScriptDirectory.from_config(sb._alembic_config()).get_current_head()
    eng = create_engine(scratch_db)
    try:
        with eng.connect() as c:
            assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == head
            # 資料必須原封不動——stamp 只改版本紀錄，不該碰業務表
            assert (
                c.execute(
                    text("SELECT count(*) FROM setting_master WHERE setting_code = '__probe__'")
                ).scalar()
                == 1
            )
    finally:
        eng.dispose()


def test_squash_targets_all_exist_in_script_directory():
    """`SQUASHED_REVISIONS` 的每個目標都必須是 script 目錄裡現存的 revision。

    這條不變式踩過：目標曾指向一個「後來又被下一次 squash 刪掉」的 baseline，
    結果 stamp 直接拋 Can't locate revision —— 把 crash-loop 換成另一種 crash-loop。
    每次 squash 都要回頭檢查舊條目的目標是否還活著。
    """
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(sb._alembic_config())
    dangling = []
    for legacy, target in sb.SQUASHED_REVISIONS.items():
        try:
            script.get_revision(target)
        except Exception:  # noqa: BLE001
            dangling.append(f"{legacy}→{target}")
    assert not dangling, (
        f"SQUASHED_REVISIONS 的目標已不存在於 script 目錄：{dangling}。"
        f"該目標可能被後續 squash 刪除——請改指向現存的 baseline。"
    )
