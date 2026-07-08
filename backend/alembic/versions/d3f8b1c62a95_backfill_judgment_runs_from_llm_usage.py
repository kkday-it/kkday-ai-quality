"""backfill judgment_runs from llm_usage（歸因歷史回填：judgment_runs 上線前的 pj_* job）

Revision ID: d3f8b1c62a95
Revises: c8e5a2d94f17
Create Date: 2026-07-07

"""

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d3f8b1c62a95"
down_revision: str | Sequence[str] | None = "c8e5a2d94f17"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """從 llm_usage 聚合回填歷史 run（僅 prejudge job：job_id LIKE 'pj\\_%' 且尚無 run 紀錄）。

    可還原欄：total/processed＝distinct source_id 數（近似，成功失敗當時未記 → ok/failed 留 NULL，
    前端顯示 —）；model＝attribute 階段最多的模型（主判決模型），退化取任一；kind＝單一標的
    視為 single、其餘 batch（scope/selected 已不可考）；起訖＝該 job 呼叫時間 min/max；狀態一律
    done（進行中 job 不可能有完整歷史 usage 卻無 run 列）。params 標 backfilled 供辨識與 downgrade。
    """
    op.execute(
        text(r"""
        INSERT INTO judgment_runs (
            job_id, kind, rejudge, source, model, ensemble_voters, params, status,
            total, processed, ok, failed, total_tokens, cost_usd, triggered_by,
            started_at, finished_at
        )
        SELECT
            u.job_id,
            CASE WHEN count(DISTINCT u.source_id) <= 1 THEN 'single' ELSE 'batch' END,
            NULL,
            max(coalesce(u.source, '')),
            coalesce(
                (SELECT u2.model FROM llm_usage u2
                 WHERE u2.job_id = u.job_id AND u2.stage IN ('attribute', 'attribute_b')
                 GROUP BY u2.model ORDER BY count(*) DESC LIMIT 1),
                max(u.model)),
            0,
            jsonb_build_object('backfilled', true),
            'done',
            count(DISTINCT u.source_id),
            count(DISTINCT u.source_id),
            NULL, NULL,
            sum(coalesce(u.total_tokens, 0)),
            sum(coalesce(u.cost_usd, 0)),
            '',
            min(u.created_at), max(u.created_at)
        FROM llm_usage u
        WHERE u.job_id LIKE 'pj\_%'
          AND NOT EXISTS (SELECT 1 FROM judgment_runs r WHERE r.job_id = u.job_id)
        GROUP BY u.job_id
    """)
    )


def downgrade() -> None:
    """移除回填列（params 帶 backfilled 標記者）。"""
    op.execute(text("""DELETE FROM judgment_runs WHERE params @> '{"backfilled": true}'::jsonb"""))
