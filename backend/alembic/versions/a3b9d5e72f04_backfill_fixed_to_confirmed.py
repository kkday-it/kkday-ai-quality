"""backfill judgments status fixed -> confirmed（撤除死狀態 fixed）

Revision ID: a3b9d5e72f04
Revises: f2a8c4d61e93
Create Date: 2026-07-11

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3b9d5e72f04"
down_revision: str | Sequence[str] | None = "f2a8c4d61e93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """既有 fixed 統一轉 confirmed。

    fixed（已修）前端零入口、無自動化流程會設它＝死狀態；status 人工處置軸簡化為
    new / auto_confirmed / confirmed / dismissed（新增「撤銷覆核回 new」路徑於 API 層）。
    須排在 f2a8c4d61e93（judgment_history 回填）之後：回填快照忠實記下轉換前的
    歷史事實，再改 live 欄——先立史書、再改現狀。
    """
    op.execute("UPDATE judgments SET status = 'confirmed' WHERE status = 'fixed'")


def downgrade() -> None:
    """不可逆 no-op：無法還原「哪些列原本是 fixed」（upgrade 前請自行備份）。"""
