"""add prompt_sandbox_runs.versions column

Revision ID: f43b7d08deb2
Revises: 87226c7ffb83
Create Date: 2026-07-15

Prompt 測試沙盒版本選擇功能：本次測試 7 條 prompt 各自用哪個版本（{rule_code: version}，
非 active 才記——active 沿用 judge_rule_versions 當下狀態，事後可回推）。取代先前草稿工作台 v2
的 overrides/baseline_run_id/active_versions/iteration_session_id/baseline_results/verdict_summary
六欄設計（草稿雙 pass 對比機制已整組拆除，所有 Prompt 測試改在歸因列表以版本選擇進行）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f43b7d08deb2"
down_revision: str | Sequence[str] | None = "87226c7ffb83"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """prompt_sandbox_runs 加 versions 欄（NOT NULL DEFAULT '{}'::jsonb，metadata-only 免全表 rewrite）。"""
    op.add_column(
        "prompt_sandbox_runs",
        sa.Column(
            "versions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """移除 versions 欄。"""
    op.drop_column("prompt_sandbox_runs", "versions")
