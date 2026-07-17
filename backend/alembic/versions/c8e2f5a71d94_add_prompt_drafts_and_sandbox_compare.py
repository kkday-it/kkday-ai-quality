"""add prompt_drafts table + prompt_sandbox_runs drafts/compare columns

Revision ID: c8e2f5a71d94
Revises: a54632b6343e
Create Date: 2026-07-16

Prompt 草稿閉環（編輯 → 沙盒單測 → 前後對比 → 選擇入庫）：
- prompt_drafts：prompt_*（7 支初判 Prompt）每 rule_code 一份共享草稿（未入庫的編輯中內容），
  與 judge_rule_versions 分離——版本表維持「存檔即 active」單一語意。
- prompt_sandbox_runs.drafts：本次測試各 prompt 的草稿 md 全文快照（run 與草稿演進脫鉤、可溯源）。
- prompt_sandbox_runs.compare：雙跑對比模式標記（true 時 results 逐筆為 baseline/draft 兩組）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8e2f5a71d94"
down_revision: str | Sequence[str] | None = "a54632b6343e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """建 prompt_drafts 表；prompt_sandbox_runs 加 drafts/compare 欄（帶 default，metadata-only）。"""
    op.create_table(
        "prompt_drafts",
        sa.Column("rule_code", sa.Text(), primary_key=True),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("base_version", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.Text()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.add_column(
        "prompt_sandbox_runs",
        sa.Column(
            "drafts",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "prompt_sandbox_runs",
        sa.Column(
            "compare",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """移除 drafts/compare 欄與 prompt_drafts 表。"""
    op.drop_column("prompt_sandbox_runs", "compare")
    op.drop_column("prompt_sandbox_runs", "drafts")
    op.drop_table("prompt_drafts")
