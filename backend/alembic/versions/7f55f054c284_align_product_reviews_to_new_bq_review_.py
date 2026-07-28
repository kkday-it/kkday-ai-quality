"""align product_reviews to new bq review-fusion schema

Revision ID: 7f55f054c284
Revises: 830a12b92fff
Create Date: 2026-07-27 06:31:00.340471

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7f55f054c284"
down_revision: str | Sequence[str] | None = "830a12b92fff"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema.

    lst_dt_go 用 alter_column 改名成 go_date（保留既有 42,770 筆資料），
    非自動生成的 drop+add（那樣會把既有出發日資料全部歸零）。
    product_category 為明確全棧退役（見 feature-retirement.md），drop 前已
    pg_dump 備份至 ~/kkday-backups/product_reviews_20260727_143024.sql。
    """
    op.alter_column("product_reviews", "lst_dt_go", new_column_name="go_date")
    op.add_column("product_reviews", sa.Column("order_lang", sa.Text(), nullable=True))
    op.add_column("product_reviews", sa.Column("order_price", sa.Text(), nullable=True))
    op.add_column("product_reviews", sa.Column("order_profit", sa.Text(), nullable=True))
    op.add_column(
        "product_reviews", sa.Column("order_create_source_code", sa.Text(), nullable=True)
    )
    op.add_column("product_reviews", sa.Column("order_create_time", sa.Text(), nullable=True))
    op.add_column("product_reviews", sa.Column("product_name", sa.Text(), nullable=True))
    op.add_column("product_reviews", sa.Column("bd_tag", sa.Text(), nullable=True))
    op.add_column("product_reviews", sa.Column("bd_tag_note", sa.Text(), nullable=True))
    op.add_column("product_reviews", sa.Column("supplier_name", sa.Text(), nullable=True))
    op.drop_index(op.f("idx_product_reviews_product_category"), table_name="product_reviews")
    op.drop_column("product_reviews", "product_category")


def downgrade() -> None:
    """Downgrade schema.

    還原欄位結構，不還原 product_category 資料（見 upgrade() docstring 的備份檔）。
    """
    op.add_column(
        "product_reviews",
        sa.Column("product_category", sa.TEXT(), autoincrement=False, nullable=True),
    )
    op.create_index(
        op.f("idx_product_reviews_product_category"),
        "product_reviews",
        ["product_category"],
        unique=False,
    )
    op.drop_column("product_reviews", "supplier_name")
    op.drop_column("product_reviews", "bd_tag_note")
    op.drop_column("product_reviews", "bd_tag")
    op.drop_column("product_reviews", "product_name")
    op.drop_column("product_reviews", "order_create_time")
    op.drop_column("product_reviews", "order_create_source_code")
    op.drop_column("product_reviews", "order_profit")
    op.drop_column("product_reviews", "order_price")
    op.drop_column("product_reviews", "order_lang")
    op.alter_column("product_reviews", "go_date", new_column_name="lst_dt_go")
