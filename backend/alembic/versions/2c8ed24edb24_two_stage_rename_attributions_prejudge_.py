"""two stage rename: attributions prejudge_runs attribution_history verdict axis

Revision ID: 2c8ed24edb24
Revises: c8e2f5a71d94
Create Date: 2026-07-17

初判分類（prejudge）/ 判決歸因（verdict）兩階段語義隔離的 DB 正名：

- 表：judgments→attributions（歸因結果）、judgment_history→attribution_history（事件流）、
  judgment_runs→prejudge_runs（初判批次）
- 欄（attributions）：stage→prejudge_stage（初判完成度）；status/status_updated_by/
  status_updated_at→verdict_status/verdict_by/verdict_at（判決軸）；DROP judged_at
  （與 created_at 同函式同值的冗余時間欄，初判時間以 attribution_history 事件為準）
- kind 值：'judgment'→'prejudge'、'status'→'verdict'（'note'/'failure'/'router_shadow' 不動）
- 索引/PK/序列**顯式逐一 RENAME**（PG RENAME TABLE 不自動改 PK 約束名與序列名；
  自動遍歷 metadata 會漏 raw-SQL partial index）；snapshot partial index 謂詞含舊 kind 值，
  須 DROP 後以新謂詞重建。

所有語句冪等（IF EXISTS / to_regclass 守衛）：dev 空庫走 create_all+stamp 不經此檔；
僅既有庫升級與舊備份還原重放會執行。
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2c8ed24edb24"
down_revision: str | Sequence[str] | None = "c8e2f5a71d94"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (舊名, 新名) 顯式清單——表
_TABLES = [
    ("judgments", "attributions"),
    ("judgment_history", "attribution_history"),
    ("judgment_runs", "prejudge_runs"),
]
# (表新名, 舊欄, 新欄)
_COLUMNS = [
    ("attributions", "stage", "prejudge_stage"),
    ("attributions", "status", "verdict_status"),
    ("attributions", "status_updated_by", "verdict_by"),
    ("attributions", "status_updated_at", "verdict_at"),
]
# (舊索引, 新索引)——普通索引（partial index 另行重建；PK 走 RENAME CONSTRAINT）
_INDEXES = [
    ("idx_judgments_source", "idx_attributions_source"),
    ("idx_judgments_source_id", "idx_attributions_source_id"),
    ("idx_judgments_polarity", "idx_attributions_polarity"),
    ("idx_judgments_stage", "idx_attributions_prejudge_stage"),
    ("idx_judgments_l1", "idx_attributions_l1"),
    ("idx_judgments_l2", "idx_attributions_l2"),
    ("idx_judgments_sentiment", "idx_attributions_sentiment"),
    ("idx_judgments_tier", "idx_attributions_tier"),
    ("idx_judgment_history_source_id", "idx_attribution_history_source_id"),
    ("idx_judgment_history_created_at", "idx_attribution_history_created_at"),
    ("idx_judgment_runs_started_at", "idx_prejudge_runs_started_at"),
]
# (表新名, 舊 PK 約束, 新 PK 約束)
_PKS = [
    ("attributions", "judgments_pkey", "attributions_pkey"),
    ("attribution_history", "judgment_history_pkey", "attribution_history_pkey"),
    ("prejudge_runs", "judgment_runs_pkey", "prejudge_runs_pkey"),
]


def upgrade() -> None:
    """兩階段正名（冪等；順序：清空殼→表→欄→索引→PK→序列→partial index 重建→kind 值遷移）。"""
    # dev 雙路徑防護：uvicorn --reload 期間 init_db(create_all) 可能已用新 metadata 建出
    # 「空的」新名表（與待改名的舊資料表並存）——新表為空且舊表仍在時，先移除空殼再改名；
    # 新表已有資料（真正跑過新 schema）則舊表不該存在，RENAME IF EXISTS 自然 no-op。
    for old, new in _TABLES:
        op.execute(
            f"""DO $$ BEGIN
                IF to_regclass('{old}') IS NOT NULL AND to_regclass('{new}') IS NOT NULL
                   AND (SELECT count(*) FROM {new}) = 0 THEN
                    EXECUTE 'DROP TABLE {new} CASCADE';
                END IF;
            END $$"""
        )
    for old, new in _TABLES:
        op.execute(f"ALTER TABLE IF EXISTS {old} RENAME TO {new}")
    for table, old, new in _COLUMNS:
        op.execute(
            f"""DO $$ BEGIN
                IF EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name = '{table}' AND column_name = '{old}') THEN
                    ALTER TABLE {table} RENAME COLUMN {old} TO {new};
                END IF;
            END $$"""
        )
    op.execute("ALTER TABLE IF EXISTS attributions DROP COLUMN IF EXISTS judged_at")
    for old, new in _INDEXES:
        op.execute(f"ALTER INDEX IF EXISTS {old} RENAME TO {new}")
    for table, old, new in _PKS:
        op.execute(
            f"""DO $$ BEGIN
                IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = '{old}') THEN
                    ALTER TABLE {table} RENAME CONSTRAINT {old} TO {new};
                END IF;
            END $$"""
        )
    op.execute(
        "ALTER SEQUENCE IF EXISTS judgment_history_id_seq RENAME TO attribution_history_id_seq"
    )
    # snapshot partial index：謂詞含舊 kind 值，改名無效——DROP 後以新謂詞/新名重建
    op.execute("DROP INDEX IF EXISTS idx_judgment_history_snapshot")
    # kind 值遷移（先遷值再建索引，避免建索引時掃到混合值）
    op.execute("UPDATE attribution_history SET kind = 'prejudge' WHERE kind = 'judgment'")
    op.execute("UPDATE attribution_history SET kind = 'verdict' WHERE kind = 'status'")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_attribution_history_snapshot "
        "ON attribution_history (source, model, source_id, created_at DESC) "
        "WHERE kind = 'prejudge'"
    )


def downgrade() -> None:
    """反向正名（不還原 judged_at 資料，加回 nullable 空欄）。"""
    op.execute("DROP INDEX IF EXISTS idx_attribution_history_snapshot")
    op.execute("UPDATE attribution_history SET kind = 'judgment' WHERE kind = 'prejudge'")
    op.execute("UPDATE attribution_history SET kind = 'status' WHERE kind = 'verdict'")
    op.execute(
        "ALTER SEQUENCE IF EXISTS attribution_history_id_seq RENAME TO judgment_history_id_seq"
    )
    for table, old, new in _PKS:
        op.execute(
            f"""DO $$ BEGIN
                IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = '{new}') THEN
                    ALTER TABLE {table} RENAME CONSTRAINT {new} TO {old};
                END IF;
            END $$"""
        )
    for old, new in _INDEXES:
        op.execute(f"ALTER INDEX IF EXISTS {new} RENAME TO {old}")
    op.execute("ALTER TABLE IF EXISTS attributions ADD COLUMN IF NOT EXISTS judged_at TEXT")
    for table, old, new in _COLUMNS:
        op.execute(
            f"""DO $$ BEGIN
                IF EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name = '{table}' AND column_name = '{new}') THEN
                    ALTER TABLE {table} RENAME COLUMN {new} TO {old};
                END IF;
            END $$"""
        )
    for old, new in _TABLES:
        op.execute(f"ALTER TABLE IF EXISTS {new} RENAME TO {old}")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_judgment_history_snapshot "
        "ON judgment_history (source, model, source_id, created_at DESC) "
        "WHERE kind = 'judgment'"
    )
