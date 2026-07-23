"""create judge_rule_versions table (retroactive — orphan table fix)

Revision ID: e2f4a8c91d37
Revises: b3e8f5a27c61
Create Date: 2026-07-23 10:00:00.000000

背景：judge_rule_versions 從未有真實 create migration（原對應的
df57def04797_judge_rule_versions.py upgrade()/downgrade() 皆為空 pass）；
表結構只活在 tables.py + create_all 產物——任何走「既有庫 alembic upgrade
head」路徑的環境（含未來 RDS）永遠拿不到這張表。不改已發布的
df57def04797（已跑過的 migration 不可變慣例），改在鏈尾端補建，
用 IF NOT EXISTS 冪等容忍既有庫已由 create_all 建過此表。
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e2f4a8c91d37"
down_revision: str | Sequence[str] | None = "b3e8f5a27c61"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS judge_rule_versions (
            id BIGSERIAL PRIMARY KEY,
            rule_code TEXT NOT NULL,
            version INTEGER NOT NULL,
            content JSONB NOT NULL,
            note TEXT,
            author TEXT,
            is_active BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMPTZ DEFAULT now(),
            CONSTRAINT uq_judge_rule_code_version UNIQUE (rule_code, version)
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_judge_rule_active
        ON judge_rule_versions (rule_code) WHERE is_active
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    raise NotImplementedError("judge_rule_versions 建表不可逆，需 downgrade 請手動評估")
