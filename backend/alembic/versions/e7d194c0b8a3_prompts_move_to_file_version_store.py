"""初判 Prompt 遷出 DB：drop prompt_drafts、清 judge_rule_versions 的 prompt_* 列

Revision ID: e7d194c0b8a3
Revises: c4e81b7a35d2
Create Date: 2026-07-28 19:25:00.000000

7 支初判 prompt 的版本與草稿已於 2026-07-28 遷至檔案版本庫
（`prompts/<id>/v<時間戳>.md` ＋ `ACTIVE`，見 `app.judge.prompt_versions`），DB 側是死資料。

依專案核心原則 4「退役即徹底」清乾淨而非留著當備份：遷移腳本已逐字核對 7 支 ACTIVE 版內容
等於原 DB active，136 版完整歷史都在 git 裡，執行前另有 pg_dump 落 `~/kkday-backups/`。
留著反而有害——`RULE_CODES` 已不含 prompt_*，這些列會變成 `list_rule_meta` docstring 警告過的
「幽靈資料」：撈不到卻仍佔著唯一索引。

⚠️ downgrade 只重建 `prompt_drafts` 表結構、**不還原任何資料**（資料已在檔案側）。真要回退請用
`~/kkday-backups/` 的 dump。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7d194c0b8a3"
down_revision: str | Sequence[str] | None = "c4e81b7a35d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PROMPT_CODES = (
    "prompt_polarity",
    "prompt_C-1",
    "prompt_C-2",
    "prompt_C-3",
    "prompt_C-4",
    "prompt_C-5",
    "prompt_C-6",
)


def upgrade() -> None:
    """清 prompt_* 版本列 + 整表移除 prompt_drafts。"""
    op.execute(
        sa.text("DELETE FROM judge_rule_versions WHERE rule_code = ANY(:codes)").bindparams(
            sa.bindparam("codes", value=list(_PROMPT_CODES), type_=postgresql.ARRAY(sa.Text))
        )
    )
    op.drop_table("prompt_drafts")


def downgrade() -> None:
    """僅重建 prompt_drafts 表結構（資料不還原，見模組 docstring）。"""
    op.create_table(
        "prompt_drafts",
        sa.Column("rule_code", sa.Text(), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("base_version", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.PrimaryKeyConstraint("rule_code"),
    )
