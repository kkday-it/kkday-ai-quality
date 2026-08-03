"""product_reviews → reviews：表改名 + 來源 code 存量值遷移

`product_reviews` 同時是「表名」與「反饋來源 code 字面值」，後者已落在多張表的 source 欄，
故改名不能只 rename table，必須連存量資料值一起搬，否則 source 對不上、列表/歸因全查不到。

處理三件事：
1. 表改名 product_reviews → reviews
2. 索引改名（PostgreSQL 的索引名不隨表自動更名，留舊名會與新表名不一致）
3. 6 張帶 source 欄的表，值 'product_reviews' → 'reviews'
   （app_feedback.source 是「來源渠道」語意不同，不在此列）

Revision ID: f4c9a2e81b57
Revises: e2b6d4f18a03
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f4c9a2e81b57"
down_revision: str | Sequence[str] | None = "e2b6d4f18a03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 存有反饋來源 code 的表（其 source 欄值需同步搬移）
_SOURCE_VALUE_TABLES: tuple[str, ...] = (
    "attributions",
    "batches",
    "llm_usage",
    "prejudge_runs",
    "attribution_history",
    "prompt_sandbox_runs",
)


def _drop_empty_shell_table() -> None:
    """清掉 metadata.create_all 搶先建出的空 reviews 表（否則 rename 撞 DuplicateTable）。

    後端啟動時會依 tables.py 定義 create_all，本次改名讓 tables.py 先有了 reviews，
    因此在遷移執行前後端只要啟動過一次，就會多出一張空的 reviews。

    僅在「表存在且零列」時移除；一旦有資料代表狀態非預期（例如遷移已跑過又被還原），
    直接拋錯中止，不做任何破壞性動作。
    """
    conn = op.get_bind()
    exists = conn.execute(
        sa.text(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'reviews'"
        )
    ).scalar()
    if not exists:
        return
    rows = conn.execute(sa.text("SELECT COUNT(*) FROM reviews")).scalar()
    if rows:
        raise RuntimeError(
            f"reviews 表已存在且有 {rows} 列資料，無法判定為 create_all 空殼——"
            "請人工確認資料歸屬後再執行本遷移。"
        )
    op.execute("DROP TABLE reviews")


def upgrade() -> None:
    """表改名 + 索引改名 + 6 表 source 值 product_reviews → reviews。"""
    _drop_empty_shell_table()
    op.rename_table("product_reviews", "reviews")
    op.execute(
        "ALTER INDEX IF EXISTS idx_product_reviews_create_date RENAME TO idx_reviews_create_date"
    )
    op.execute("ALTER INDEX IF EXISTS idx_product_reviews_prod_oid RENAME TO idx_reviews_prod_oid")
    for table in _SOURCE_VALUE_TABLES:
        op.execute(f"UPDATE {table} SET source = 'reviews' WHERE source = 'product_reviews'")  # noqa: S608


def downgrade() -> None:
    """還原表名、索引名與 source 值。"""
    for table in _SOURCE_VALUE_TABLES:
        op.execute(f"UPDATE {table} SET source = 'product_reviews' WHERE source = 'reviews'")  # noqa: S608
    op.execute(
        "ALTER INDEX IF EXISTS idx_reviews_create_date RENAME TO idx_product_reviews_create_date"
    )
    op.execute("ALTER INDEX IF EXISTS idx_reviews_prod_oid RENAME TO idx_product_reviews_prod_oid")
    op.rename_table("reviews", "product_reviews")
