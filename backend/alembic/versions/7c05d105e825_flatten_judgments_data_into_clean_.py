"""flatten judgments data into clean grouped object + drop dead columns

Revision ID: 7c05d105e825
Revises: 209f902fd979
Create Date: 2026-07-05 14:46:09.585335

把 judgments.data（Text/JSON）攤平重整成乾淨分組物件、刪除殘留/幽靈/legacy 屬性與重複 scalar 欄。
形狀 SSOT＝app.core.schema.TicketFinding.to_stored；查詢層抽欄＝app.core.db._shared.d_*。
詳計畫見 plans/1-peaceful-wirth.md。遷移前已 pg_dump 全庫 + judgments（backend/_backup/）。
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7c05d105e825"
down_revision: str | Sequence[str] | None = "209f902fd979"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 攤平時移除的重複 scalar 欄（值皆與 data 內同名 key 重複，或恆空/未被讀取）。
_DROP_COLS = [
    "pkg_oid",  # 冗餘（source_id join 源表可得，且未被任何讀取路徑使用）
    "confidence",  # 併入 data.confidence.value
    "raw_confidence",  # 併入 data.confidence.raw
    "is_enhanced",  # 恆 0，未讀取
    "enhance_model",  # 恆空，未讀取
    "suspected_field",  # 恆 none，未讀取
    "recommended_action",  # 併入 data.content.action
]

# data 攤平重整：舊扁平 ~54 key → 乾淨分組物件（僅 14 真訊號欄，殘留/幽靈/legacy 一律丟棄）。
_REGROUP_SQL = """
UPDATE judgments SET data = (
  jsonb_build_object(
    'polarity', COALESCE(data::jsonb->>'polarity', ''),
    'stage', COALESCE(data::jsonb->>'judgment_stage', ''),
    'attribution', jsonb_build_object(
      'l1', jsonb_build_object('code', COALESCE(data::jsonb->>'l1_domain_code', ''), 'label', COALESCE(data::jsonb->>'l1_label', '')),
      'l2', jsonb_build_object('code', COALESCE(data::jsonb->>'l2_code', ''), 'label', COALESCE(data::jsonb->>'l2_label', '')),
      'l3', jsonb_build_object('code', COALESCE(data::jsonb->>'l3_code', ''), 'label', COALESCE(data::jsonb->>'l3_label', ''))
    ),
    'confidence', jsonb_build_object(
      'value', COALESCE((data::jsonb->>'confidence')::float, 0),
      'raw', COALESCE((data::jsonb->>'raw_confidence')::float, 0),
      'tier', COALESCE(data::jsonb->>'confidence_tier', '')
    ),
    'content', jsonb_build_object(
      'summary', COALESCE(data::jsonb->>'problem_summary', ''),
      'evidence', COALESCE(data::jsonb->>'evidence_quote', ''),
      'action', COALESCE(data::jsonb->>'recommended_action', '')
    ),
    'meta', jsonb_build_object(
      'model', COALESCE(data::jsonb->>'model_used', ''),
      'primary', COALESCE((data::jsonb->>'is_primary')::boolean, false),
      'judgedAt', COALESCE(data::jsonb->>'judged_at', '')
    )
  )
)::text
WHERE data IS NOT NULL AND data <> '';
"""

# 列表深化篩選熱路徑索引（data 分組物件的 JSONB expression；text→jsonb cast 為 IMMUTABLE，可建索引）。
_INDEXES = {
    "idx_judgments_polarity": "((data::jsonb->>'polarity'))",
    "idx_judgments_stage": "((data::jsonb->>'stage'))",
    "idx_judgments_l1": "((data::jsonb->'attribution'->'l1'->>'code'))",
    "idx_judgments_tier": "((data::jsonb->'confidence'->>'tier'))",
}


def upgrade() -> None:
    """攤平 data → 分組物件 → drop 重複/死欄 → 建 JSONB expression 索引。"""
    op.execute(_REGROUP_SQL)
    op.execute(
        "ALTER TABLE judgments " + ", ".join(f"DROP COLUMN IF EXISTS {c}" for c in _DROP_COLS)
    )
    for name, expr in _INDEXES.items():
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON judgments {expr}")


def downgrade() -> None:
    """回滾：drop 新索引 + 加回 scalar 欄 + data 攤平回舊扁平 key（best-effort；精確還原以 pg_dump 為準）。"""
    for name in _INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {name}")
    op.execute(
        "ALTER TABLE judgments "
        "ADD COLUMN IF NOT EXISTS pkg_oid text, "
        "ADD COLUMN IF NOT EXISTS confidence double precision, "
        "ADD COLUMN IF NOT EXISTS raw_confidence double precision, "
        "ADD COLUMN IF NOT EXISTS is_enhanced integer DEFAULT 0, "
        "ADD COLUMN IF NOT EXISTS enhance_model text, "
        "ADD COLUMN IF NOT EXISTS suspected_field text, "
        "ADD COLUMN IF NOT EXISTS recommended_action text"
    )
    # data 分組物件 → 舊扁平 key（回填 scalar 欄 confidence 由 data.confidence.value）
    op.execute(
        """
        UPDATE judgments SET
          confidence = (data::jsonb->'confidence'->>'value')::float,
          raw_confidence = (data::jsonb->'confidence'->>'raw')::float,
          recommended_action = data::jsonb->'content'->>'action',
          data = (
            jsonb_build_object(
              'polarity', data::jsonb->>'polarity',
              'judgment_stage', data::jsonb->>'stage',
              'l1_domain_code', data::jsonb->'attribution'->'l1'->>'code',
              'l1_label', data::jsonb->'attribution'->'l1'->>'label',
              'l2_code', data::jsonb->'attribution'->'l2'->>'code',
              'l2_label', data::jsonb->'attribution'->'l2'->>'label',
              'l3_code', data::jsonb->'attribution'->'l3'->>'code',
              'l3_label', data::jsonb->'attribution'->'l3'->>'label',
              'confidence', (data::jsonb->'confidence'->>'value')::float,
              'raw_confidence', (data::jsonb->'confidence'->>'raw')::float,
              'confidence_tier', data::jsonb->'confidence'->>'tier',
              'problem_summary', data::jsonb->'content'->>'summary',
              'evidence_quote', data::jsonb->'content'->>'evidence',
              'recommended_action', data::jsonb->'content'->>'action',
              'model_used', data::jsonb->'meta'->>'model',
              'is_primary', (data::jsonb->'meta'->>'primary')::boolean,
              'judged_at', data::jsonb->'meta'->>'judgedAt'
            )
          )::text
        WHERE data IS NOT NULL AND data <> '';
        """
    )
