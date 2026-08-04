"""Alembic 環境：target_metadata + 連線皆取自 app 的 tables 模組（config 驅動 URL）。

URL 來源＝`tables.resolve_url()`（＝`config.env.database_url`，PostgreSQL；dev 預設本機，prod 經 DATABASE_URL 覆蓋），
與後端 runtime 同一條 SSOT；故 `alembic upgrade` 跑在哪個庫由 DATABASE_URL 決定，不在 alembic.ini 硬寫。
"""

from logging.config import fileConfig

from alembic import context
from app.core.db import tables

config = context.config
if config.config_file_name is not None:
    # disable_existing_loggers=False 必要：預設 True 會把「此刻已存在的 logger」全部停用。
    # alembic 以 in-process 方式被呼叫時（`schema_bootstrap.align_schema`、測試），這會靜默
    # 掐掉 app 自己的 logger——實測會讓 `app.core.settings` 之後的 warning 完全發不出來。
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = tables.metadata


def run_migrations_offline() -> None:
    """offline：只用 URL 產 SQL，不建連線。"""
    context.configure(
        url=tables.resolve_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """online：複用 app 的 config 驅動 engine（同一條 DATABASE_URL）。"""
    connectable = tables.get_engine()
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            # 每支 migration 各自一個交易（預設是整條 upgrade 共用單一交易）。
            # 這是 `op.get_context().autocommit_block()` 有正確語義的前提——`CREATE INDEX
            # CONCURRENTLY` 不能在交易內執行，autocommit_block 會先 COMMIT 前置交易再切
            # AUTOCOMMIT；若整條鏈是單一交易，那個 COMMIT 會把前面每一支都一併提交，
            # 導致後續失敗時無法整體回滾（半套狀態最難救）。
            transaction_per_migration=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
