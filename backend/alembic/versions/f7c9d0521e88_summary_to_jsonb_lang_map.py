"""summary Text -> JSONB 語系 map（既有字串 wrap 成 {zh-tw: str}）

Revision ID: f7c9d0521e88
Revises: e6b3c81f9a24
Create Date: 2026-07-06

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f7c9d0521e88"
down_revision: Union[str, Sequence[str], None] = "e6b3c81f9a24"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """judgments.summary Text→JSONB：反饋摘要改語系→簡明摘要 map（務必含 zh-tw；表格只顯示 zh-tw）。

    既有純字串摘要（含已由 Claude 翻成繁中的 63 筆）一律 wrap 成 {"zh-tw": <原字串>}；空/NULL→NULL。
    """
    op.execute(
        """
        ALTER TABLE judgments
        ALTER COLUMN summary TYPE jsonb
        USING (
            CASE WHEN summary IS NULL OR summary = '' THEN NULL
                 ELSE jsonb_build_object('zh-tw', summary) END
        )
        """
    )


def downgrade() -> None:
    """JSONB→Text：取回 zh-tw 值作純字串（其餘語系版本於降級時捨棄）。"""
    op.execute(
        """
        ALTER TABLE judgments
        ALTER COLUMN summary TYPE text
        USING (CASE WHEN summary IS NULL THEN NULL ELSE (summary->>'zh-tw') END)
        """
    )
