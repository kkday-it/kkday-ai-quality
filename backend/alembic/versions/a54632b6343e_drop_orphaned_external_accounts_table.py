"""drop orphaned external_accounts table

Google OAuth 整合已退場（GCP 組織權限限制無法完成憑證申請，見 backup/google-oauth-wip-20260716
分支保留完整實作）。表定義已從 tables.py 移除，但 dev DB 因先前 metadata.create_all 已建出此表，
需顯式 drop 清除孤兒表；drop 前已 pg_dump 備份至 ~/kkday-backups/。

Revision ID: a54632b6343e
Revises: f43b7d08deb2
Create Date: 2026-07-16

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a54632b6343e"
down_revision: str | Sequence[str] | None = "f43b7d08deb2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """移除孤兒表（冪等：IF EXISTS）。"""
    op.execute("DROP TABLE IF EXISTS external_accounts")


def downgrade() -> None:
    """不還原資料（僅供緊急回退表結構；若要恢復功能請改從 backup 分支取用完整實作）。"""
    pass
