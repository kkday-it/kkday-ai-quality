"""add evidence_snapshot table

Revision ID: 5c7d2e91ab34
Revises: 2c8ed24edb24
Create Date: 2026-07-22

訂單佐證快取改落本地 PG（取代 diskcache 檔案快取，使用者 2026-07-22 拍板）：

- evidence_snapshot：qc_evidence 兩級快取（下單當時投影快照 + TTL）的 KV 儲存（cache_key PK / kind / payload JSONB /
  fetched_at / expires_at ISO UTC）＋ expires 索引（懶清理走此）
- 刻意不入 datapack TABLE_LOAD_ORDER（runtime 派生、可重生、不隨資料包外流）

冪等（IF NOT EXISTS）：dev 空庫走 create_all+stamp 不經此檔；僅既有庫升級執行。
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5c7d2e91ab34"
down_revision: str | Sequence[str] | None = "2c8ed24edb24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """建 evidence_snapshot 表 + expires 索引。"""
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS evidence_snapshot (
            cache_key TEXT PRIMARY KEY,
            kind TEXT,
            payload JSONB,
            fetched_at TEXT,
            expires_at TEXT
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_evidence_snapshot_expires ON evidence_snapshot (expires_at)"
    )


def downgrade() -> None:
    """回滾：整表移除（純快取，無資料保留需求）。"""
    op.execute("DROP TABLE IF EXISTS evidence_snapshot")
