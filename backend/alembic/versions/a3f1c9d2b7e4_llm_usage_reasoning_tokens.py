"""llm_usage 加 reasoning_tokens 欄（量測 reasoning model 的 completion 中 reasoning 占比）

Revision ID: a3f1c9d2b7e4
Revises: f7c9d0521e88
Create Date: 2026-07-06

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3f1c9d2b7e4"
down_revision: str | Sequence[str] | None = "f7c9d0521e88"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """llm_usage.reasoning_tokens：completion_tokens 中的 reasoning 部分（gpt-5 reasoning_effort 產）。

    純觀測欄，供量測「降 reasoning_effort 可省多少 token」。dev app reload 的 create_all 可能已先建欄，
    故用 IF NOT EXISTS 冪等加欄（避免撞 DuplicateColumn，比照 checkfirst 精神）。
    """
    op.execute("ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS reasoning_tokens integer")


def downgrade() -> None:
    op.execute("ALTER TABLE llm_usage DROP COLUMN IF EXISTS reasoning_tokens")
