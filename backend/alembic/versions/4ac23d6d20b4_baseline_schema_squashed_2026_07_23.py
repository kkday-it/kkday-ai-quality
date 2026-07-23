"""baseline schema（squash 2026-07-23，取代 bd77052f7222 起的 53 個增量 migration）

Revision ID: 4ac23d6d20b4
Revises:
Create Date: 2026-07-23

"""

from collections.abc import Sequence

from alembic import op
from app.core.db import tables as T

# revision identifiers, used by Alembic.
revision: str = "4ac23d6d20b4"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """一次建齊現有全部 16 張表（attributions/product_reviews/conversations/freshdesk_tickets/
    app_feedback/mixpanel_tracker/batches/settings/judge_rule_versions/prompt_drafts/
    finding_notes/llm_usage/prejudge_runs/attribution_history/prompt_sandbox_runs/evidence_snapshot）。

    以 `T.metadata`（單一 SSOT）驅動，取代 bd77052f7222 起累積的 53 個增量檔；
    `checkfirst=True` 與既有單表 migration 慣例一致，避免與已存在庫的 DuplicateTable 衝突。
    """
    bind = op.get_bind()
    T.metadata.create_all(bind, checkfirst=True)


def downgrade() -> None:
    """移除本 baseline 建立的全部表（squash 前的舊版本歷史已不可回溯，僅能整批退回空庫）。"""
    bind = op.get_bind()
    T.metadata.drop_all(bind, checkfirst=True)
