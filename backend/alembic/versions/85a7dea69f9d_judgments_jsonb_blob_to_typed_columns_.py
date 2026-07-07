"""judgments jsonb blob to typed columns (best architecture)

Revision ID: 85a7dea69f9d
Revises: 7c05d105e825
Create Date: 2026-07-05 15:11:16.832744

最佳架構：判決表 = 查詢/聚合/篩選密集的分析核心且 schema 已穩定、資料同質 → 最佳解為 typed
scalar 欄（可直接 btree 索引、SQL 乾淨），巢狀物件屬呈現層於 API DTO 組。本遷移把上一版的
JSONB `data` 分組 blob 攤成 typed 欄、廢除 blob 與 JSONB expression 索引、改 btree 索引。
DTO SSOT＝app.core.db._shared.attribution_dto。遷移前已 pg_dump（backend/_backup/）。
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "85a7dea69f9d"
down_revision: str | Sequence[str] | None = "7c05d105e825"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 新增 typed 判決欄（型別對齊 tables.judgments）。
_ADD_COLS = [
    "polarity text",
    "stage text",
    "l1_code text",
    "l1_label text",
    "l2_code text",
    "l2_label text",
    "l3_code text",
    "l3_label text",
    "conf_value double precision",
    "conf_raw double precision",
    "conf_tier text",
    "summary text",
    "evidence text",
    "action text",
    "model text",
    "is_primary boolean DEFAULT false",
    "judged_at text",
]

# 從 JSONB data 分組物件回填 typed 欄。
_BACKFILL = """
UPDATE judgments SET
  polarity   = data::jsonb->>'polarity',
  stage      = data::jsonb->>'stage',
  l1_code    = data::jsonb->'attribution'->'l1'->>'code',
  l1_label   = data::jsonb->'attribution'->'l1'->>'label',
  l2_code    = data::jsonb->'attribution'->'l2'->>'code',
  l2_label   = data::jsonb->'attribution'->'l2'->>'label',
  l3_code    = data::jsonb->'attribution'->'l3'->>'code',
  l3_label   = data::jsonb->'attribution'->'l3'->>'label',
  conf_value = (data::jsonb->'confidence'->>'value')::float,
  conf_raw   = (data::jsonb->'confidence'->>'raw')::float,
  conf_tier  = data::jsonb->'confidence'->>'tier',
  summary    = data::jsonb->'content'->>'summary',
  evidence   = data::jsonb->'content'->>'evidence',
  action     = data::jsonb->'content'->>'action',
  model      = data::jsonb->'meta'->>'model',
  is_primary = COALESCE((data::jsonb->'meta'->>'primary')::boolean, false),
  judged_at  = data::jsonb->'meta'->>'judgedAt'
WHERE data IS NOT NULL AND data <> '';
"""

_OLD_JSONB_INDEXES = [
    "idx_judgments_polarity",
    "idx_judgments_stage",
    "idx_judgments_l1",
    "idx_judgments_tier",
]

# typed 欄 btree 索引（列表深化篩選熱路徑）。
_BTREE_INDEXES = {
    "idx_judgments_polarity": "polarity",
    "idx_judgments_stage": "stage",
    "idx_judgments_l1": "l1_code",
    "idx_judgments_tier": "conf_tier",
}


def upgrade() -> None:
    """JSONB blob → typed 欄：加欄 → 回填 → needs_review 轉 bool → drop JSONB 索引/data → btree 索引。"""
    op.execute(
        "ALTER TABLE judgments " + ", ".join(f"ADD COLUMN IF NOT EXISTS {c}" for c in _ADD_COLS)
    )
    op.execute(_BACKFILL)
    # needs_review integer(0/1) → boolean
    op.execute(
        "ALTER TABLE judgments ALTER COLUMN needs_review DROP DEFAULT, "
        "ALTER COLUMN needs_review TYPE boolean USING (needs_review <> 0), "
        "ALTER COLUMN needs_review SET DEFAULT false"
    )
    for name in _OLD_JSONB_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {name}")
    op.execute("ALTER TABLE judgments DROP COLUMN IF EXISTS data")
    for name, col in _BTREE_INDEXES.items():
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON judgments ({col})")


def downgrade() -> None:
    """回滾：重建 data(JSONB blob text) 由 typed 欄組回 → drop typed 欄/btree 索引 → 復原 JSONB 索引。"""
    op.execute("ALTER TABLE judgments ADD COLUMN IF NOT EXISTS data text")
    op.execute(
        """
        UPDATE judgments SET data = (
          jsonb_build_object(
            'polarity', COALESCE(polarity, ''),
            'stage', COALESCE(stage, ''),
            'attribution', jsonb_build_object(
              'l1', jsonb_build_object('code', COALESCE(l1_code, ''), 'label', COALESCE(l1_label, '')),
              'l2', jsonb_build_object('code', COALESCE(l2_code, ''), 'label', COALESCE(l2_label, '')),
              'l3', jsonb_build_object('code', COALESCE(l3_code, ''), 'label', COALESCE(l3_label, ''))
            ),
            'confidence', jsonb_build_object('value', COALESCE(conf_value, 0), 'raw', COALESCE(conf_raw, 0), 'tier', COALESCE(conf_tier, '')),
            'content', jsonb_build_object('summary', COALESCE(summary, ''), 'evidence', COALESCE(evidence, ''), 'action', COALESCE(action, '')),
            'meta', jsonb_build_object('model', COALESCE(model, ''), 'primary', COALESCE(is_primary, false), 'judgedAt', COALESCE(judged_at, ''))
          )
        )::text;
        """
    )
    for name in _BTREE_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {name}")
    op.execute(
        "ALTER TABLE judgments "
        + ", ".join(f"DROP COLUMN IF EXISTS {c.split()[0]}" for c in _ADD_COLS)
    )
    op.execute(
        "ALTER TABLE judgments ALTER COLUMN needs_review DROP DEFAULT, "
        "ALTER COLUMN needs_review TYPE integer USING (needs_review::int), "
        "ALTER COLUMN needs_review SET DEFAULT 0"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_judgments_polarity ON judgments ((data::jsonb->>'polarity'))"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_judgments_stage ON judgments ((data::jsonb->>'stage'))"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_judgments_l1 ON judgments ((data::jsonb->'attribution'->'l1'->>'code'))"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_judgments_tier ON judgments ((data::jsonb->'confidence'->>'tier'))"
    )
