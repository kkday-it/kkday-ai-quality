"""prompt_debug_reviews

Revision ID: b7c41e9d2a53
Revises: 9cb90ab176c4
Create Date: 2026-07-28 14:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7c41e9d2a53"
down_revision: str | Sequence[str] | None = "9cb90ab176c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """建 Prompt 調試台人工評判案例庫（新表，無既有資料遷移）。"""
    op.create_table(
        "prompt_debug_reviews",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("conversation", sa.Text(), nullable=False),
        sa.Column("ai_output", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "corrections",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "confirmed",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("comment", sa.Text(), server_default="", nullable=False),
        sa.Column("prompt_version", sa.Text(), server_default="", nullable=False),
        sa.Column("model", sa.Text(), server_default="", nullable=False),
        sa.Column("reviewer", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_prompt_debug_reviews_created", "prompt_debug_reviews", ["created_at"])


def downgrade() -> None:
    """整表移除（案例庫為本功能專屬，無其他消費端）。"""
    op.drop_index("idx_prompt_debug_reviews_created", table_name="prompt_debug_reviews")
    op.drop_table("prompt_debug_reviews")
