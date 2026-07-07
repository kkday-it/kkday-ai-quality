"""create product_reviews table

Revision ID: 3771110d1d2d
Revises: c7ae2e2be254
Create Date: 2026-07-01 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3771110d1d2d"
down_revision: str | Sequence[str] | None = "c7ae2e2be254"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """建立 product_reviews 獨立實體表（從 intake_items 拆出的商品評論來源）。

    PK 命名 xid（非 id/oid）：避開來源自身 rec_oid / order_oid 等欄位撞名。
    source_record_id / item_id 皆 UNIQUE（自然鍵 + 決定性生成鍵，供 upsert 衝突目標）。
    4 個索引對齊查詢熱路徑：score/product_category_main（list_problems 篩選）、
    occurred_at（分頁排序）、prod_oid（商品維度下鑽）。無 backfill（見另一 migration 的
    scripts/backfill_product_reviews.py 一次性腳本，不在此 migration 內執行）。
    """
    op.create_table(
        "product_reviews",
        sa.Column("xid", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_record_id", sa.Text(), nullable=True),
        sa.Column("item_id", sa.Text(), nullable=True),
        sa.Column("member_uuid", sa.Text(), nullable=True),
        sa.Column("traveller_type", sa.Text(), nullable=True),
        sa.Column("lang", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("prod_oid", sa.Text(), nullable=True),
        sa.Column("pkg_oid", sa.Text(), nullable=True),
        sa.Column("order_oid", sa.Text(), nullable=True),
        sa.Column("order_mid", sa.Text(), nullable=True),
        sa.Column("supplier_oid", sa.Text(), nullable=True),
        sa.Column("product_category_main", sa.Text(), nullable=True),
        sa.Column("product_category_sub", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("go_date", sa.Text(), nullable=True),
        sa.Column("prod_name_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=True),
        sa.Column("raw", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("xid", name=op.f("pk_product_reviews")),
        sa.UniqueConstraint("source_record_id", name="uq_product_reviews_source_record_id"),
        sa.UniqueConstraint("item_id", name="uq_product_reviews_item_id"),
    )
    op.create_index("idx_product_reviews_score", "product_reviews", ["score"], unique=False)
    op.create_index(
        "idx_product_reviews_category_main",
        "product_reviews",
        ["product_category_main"],
        unique=False,
    )
    op.create_index(
        "idx_product_reviews_occurred_at", "product_reviews", ["occurred_at"], unique=False
    )
    op.create_index("idx_product_reviews_prod_oid", "product_reviews", ["prod_oid"], unique=False)


def downgrade() -> None:
    """刪除 product_reviews 表（連同索引，PG DROP TABLE 自動級聯移除表上索引）。"""
    op.drop_index("idx_product_reviews_prod_oid", table_name="product_reviews")
    op.drop_index("idx_product_reviews_occurred_at", table_name="product_reviews")
    op.drop_index("idx_product_reviews_category_main", table_name="product_reviews")
    op.drop_index("idx_product_reviews_score", table_name="product_reviews")
    op.drop_table("product_reviews")
