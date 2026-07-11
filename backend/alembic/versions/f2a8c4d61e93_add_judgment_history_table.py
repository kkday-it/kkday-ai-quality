"""add judgment_history table (評論級判決歷史) + 回填既有已判評論初始快照

Revision ID: f2a8c4d61e93
Revises: e1f7a2c95d38
Create Date: 2026-07-11

"""

from collections.abc import Sequence

from alembic import op
from app.core.db import tables as T

# revision identifiers, used by Alembic.
revision: str = "f2a8c4d61e93"
down_revision: str | Sequence[str] | None = "e1f7a2c95d38"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """建評論級判決歷史表 + 回填既有已判評論一筆初始 kind='judgment' 快照。

    建表用 `T.judgment_history.create(bind, checkfirst=True)`（dev 環境 app 啟動的
    metadata.create_all 可能已先建出此表，checkfirst 避開 DuplicateTable，仿 c8e5a2d94f17）。

    回填：judgments 每個 distinct (source, source_id) 聚合一筆快照——
    - created_at 用該組 judged_at（時間軸忠實反映歷史判決時間，非 migration 執行時間）；
      judged_at 為 Python isoformat 帶時區字串，::timestamptz 可直接 cast。
    - params 標 {"backfilled": true}（沿用 judgment_runs 的 backfilled 標記慣例），
      result_digest 留空——下一次真判決的 (model, params, digest) 必與回填列不同，
      去重比對不會誤 skip。
    - 冪等：NOT EXISTS 擋重跑（dev 先 create_all 再 upgrade 的雙路徑下不重複回填）。
    """
    bind = op.get_bind()
    T.judgment_history.create(bind, checkfirst=True)
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
    """移除評論級判決歷史表（回填資料隨表刪除）。"""
    bind = op.get_bind()
    T.judgment_history.drop(bind, checkfirst=True)
