"""drop users.password_hash (retired: no local login system)

Revision ID: a7c31f0e4d92
Revises: 5c7d2e91ab34
Create Date: 2026-07-22 22:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c31f0e4d92"
down_revision: str | Sequence[str] | None = "5c7d2e91ab34"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_column("users", "password_hash")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column("users", sa.Column("password_hash", sa.Text(), nullable=True))
