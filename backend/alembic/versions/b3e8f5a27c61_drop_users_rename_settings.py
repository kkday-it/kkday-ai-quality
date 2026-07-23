"""drop users table (retired) + rename user_settings -> settings (key column)

Revision ID: b3e8f5a27c61
Revises: a7c31f0e4d92
Create Date: 2026-07-22 22:40:00.000000

去帳戶系統收尾：users 表零消費者（身分僅 email、稽核欄位皆存 email 字串、
be2 verifier 不再落庫）→ 整表退役；user_settings 實為全項目共享單例設定
（唯一 row key=__global__），改名 settings、PK 欄 user_id 改名 key 以符語義。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3e8f5a27c61"
down_revision: str | Sequence[str] | None = "a7c31f0e4d92"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # dev --reload 競態防禦：tables.py 更新後 uvicorn 重啟 → init_db(create_all) 可能已
    # 搶先建出「空的」新 settings 表，rename 會撞名——空新表且舊表仍在時先清掉再改名。
    op.execute(
        """
        DO $$
        BEGIN
          IF to_regclass('public.user_settings') IS NOT NULL
             AND to_regclass('public.settings') IS NOT NULL
             AND NOT EXISTS (SELECT 1 FROM settings) THEN
            DROP TABLE settings;
          END IF;
        END $$;
        """
    )
    # IF EXISTS 冪等：fresh DB 由 init_db(create_all) 直接建新 schema 後 stamp head，不經此處
    op.execute("DROP TABLE IF EXISTS users")
    op.execute("ALTER TABLE IF EXISTS user_settings RENAME TO settings")
    op.execute("ALTER TABLE IF EXISTS settings RENAME COLUMN user_id TO key")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("ALTER TABLE settings RENAME COLUMN key TO user_id")
    op.execute("ALTER TABLE IF EXISTS settings RENAME TO user_settings")
    op.create_table(
        "users",
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("user_id"),
        sa.UniqueConstraint("email"),
    )
