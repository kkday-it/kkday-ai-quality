"""add judgment_history table (評論級判決歷史) + 回填既有已判評論初始快照

Revision ID: f2a8c4d61e93
Revises: e1f7a2c95d38
Create Date: 2026-07-11

DDL 內嵌本檔（不 import tables.py）：該表其後更名為 attribution_history，live metadata
已無 judgment_history 屬性，引用 T.judgment_history 會使舊庫重放遷移鏈時 AttributeError。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f2a8c4d61e93"
down_revision: str | Sequence[str] | None = "e1f7a2c95d38"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """建評論級判決歷史表 + 回填既有已判評論一筆初始 kind='judgment' 快照。

    建表帶 has_table 守衛（dev 環境 app 啟動的 metadata.create_all 可能已先建出此表，
    等價原 checkfirst 語意）；回填**無論建表與否都跑**（自身 NOT EXISTS 冪等——dev 先
    create_all 再 upgrade 的雙路徑下不重複回填）。

    回填：judgments 每個 distinct (source, source_id) 聚合一筆快照——
    - created_at 用該組 judged_at（時間軸忠實反映歷史判決時間，非 migration 執行時間）。
    - params 標 {"backfilled": true}，result_digest 留空——下一次真判決的
      (model, params, digest) 必與回填列不同，去重比對不會誤 skip。
    """
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("judgment_history"):
        op.create_table(
            "judgment_history",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("source", sa.Text(), nullable=False),
            sa.Column("source_id", sa.Text(), nullable=False),
            sa.Column("kind", sa.Text(), nullable=False),
            sa.Column("model", sa.Text()),
            sa.Column("params", JSONB()),
            sa.Column("attributions", JSONB()),
            sa.Column("result_digest", sa.Text()),
            sa.Column("job_id", sa.Text()),
            sa.Column("triggered_by", sa.Text()),
            sa.Column("author", sa.Text()),
            sa.Column("content", sa.Text()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index(
            "idx_judgment_history_source_id",
            "judgment_history",
            ["source", "source_id", "created_at"],
        )
        op.create_index("idx_judgment_history_created_at", "judgment_history", ["created_at"])
    op.execute(
        """
        INSERT INTO judgment_history
            (source, source_id, kind, model, params, attributions, result_digest, created_at)
        SELECT
            j.source,
            j.source_id,
            'judgment',
            max(j.model),
            '{"backfilled": true}'::jsonb,
            jsonb_agg(
                jsonb_build_object(
                    'finding_id', j.finding_id,
                    'polarity', j.polarity,
                    'sentiment_score', j.sentiment_score,
                    'stage', j.stage,
                    'l1', jsonb_build_object('code', j.l1_code, 'label', j.l1_label),
                    'l2', jsonb_build_object('code', j.l2_code, 'label', j.l2_label),
                    'l3', jsonb_build_object('code', j.l3_code, 'label', j.l3_label),
                    'confidence', jsonb_build_object(
                        'value', j.conf_value, 'raw', j.conf_raw, 'tier', j.conf_tier
                    ),
                    'content', jsonb_build_object(
                        'summary', j.summary, 'evidence', j.evidence, 'action', j.action
                    ),
                    'is_primary', j.is_primary
                )
                ORDER BY j.is_primary DESC, j.finding_id
            ),
            '',
            COALESCE(max(NULLIF(j.judged_at, ''))::timestamptz, now())
        FROM judgments j
        WHERE NOT EXISTS (
            SELECT 1 FROM judgment_history h
            WHERE h.source = j.source AND h.source_id = j.source_id AND h.kind = 'judgment'
        )
        GROUP BY j.source, j.source_id
        """
    )


def downgrade() -> None:
    """移除評論級判決歷史表（回填資料隨表刪除；冪等）。"""
    op.execute("DROP TABLE IF EXISTS judgment_history")
