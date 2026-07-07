"""add finding_notes table (per-attribution append-only notes)

Revision ID: d5a2f9c34b16
Revises: c4f1a7d20e83
Create Date: 2026-07-06

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5a2f9c34b16"
down_revision: str | Sequence[str] | None = "c4f1a7d20e83"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """建歸因備註表（append-only 歷史：finding_id 對應歸因·記備註人/時間/內容）。

    獨立表：重判（replace_source_findings 刪+插 judgments）不影響備註（依 finding_id 關聯，同域重判 id 不變）。
    """
    op.create_table(
        "finding_notes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("finding_id", sa.Text(), nullable=False),
        sa.Column("author", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_finding_notes_finding", "finding_notes", ["finding_id"])


def downgrade() -> None:
    """移除歸因備註表。"""
    op.drop_index("idx_finding_notes_finding", table_name="finding_notes")
    op.drop_table("finding_notes")
