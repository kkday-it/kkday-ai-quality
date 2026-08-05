"""審計欄 varchar(36) → varchar(255)，並刪除唯一一個真正冗餘的單欄索引

## 1. 審計欄放寬（14 欄 / 8 表）

`c4e8a1f6b092` 把 `create_user` / `modify_user` / `author` 收成 `varchar(36)`，理由是「實測最長
36、零筆超過」。但 36 正是**當前最長值本身**（`judge_rule_version_lst` 的
`system:conversations-30col-migration`），安全邊際為零；而寫入端（`api/routers/v1/*.py` 的
`user.get("email", "")`、腳本傳入的 `system:*` 標記）**沒有任何截斷守衛**，多一個字元就是
`StringDataRightTruncation` → HTTP 500。

放寬為 255＝規範原文對 email 欄位的長度（RFC 5321 的 email 位址上限 254，255 恰好涵蓋），
也是 DDL 對齊計畫「豁免 #2」已向 DBA 申請的值——`c4e8a1f6b092` 反而把它做成了 36，方向做反。
PG 的 `varchar(n)` 底層與 `text` 同為 varlena，長度上限不影響儲存 / 索引 / 效能，只差一個
長度 CHECK，放寬零成本。

## 2. 刪除 `idx_attribution_tbl_feedback_source_code`

它是 `idx_attribution_tbl_mix01(feedback_source_code, source_id)` 與
`idx_attribution_tbl_unique01(feedback_source_code, source_id, l1_code, l2_code)` 的**前綴欄索引**，
兩者都能服務單獨的 `feedback_source_code` 述詞。實測（rollback 交易內 DROP 後 EXPLAIN ANALYZE）：
planner 改走 `unique01` / `mix01`，仍是 Index Only Scan、未退化為 Seq Scan；且該索引
`pg_stat_user_indexes.idx_scan` 生涯累計為 0。

⚠️ 同表另外 6 個單欄索引（polarity / prejudge_stage / l1_code / l2_code / sentiment_score /
conf_tier）**刻意保留**——計畫原本要一併刪除，但實測推翻了它的前提，理由見 `tables.py`
該組索引上方的註釋。

Revision ID: f7c3a91d6b48
Revises: c8a3e71f0b64
Create Date: 2026-08-05

"""

from collections.abc import Sequence

from alembic import op

revision: str = "f7c3a91d6b48"
down_revision: str | Sequence[str] | None = "c8a3e71f0b64"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (表, 欄) —— 全部 14 個 varchar(36) 審計欄；皆無 DEFAULT，故不需連帶重設預設值的型別標註
_AUDIT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("attribution_tbl", "create_user"),
    ("attribution_tbl", "modify_user"),
    ("attribution_event_lst", "create_user"),
    ("attribution_event_lst", "author"),
    ("evidence_snapshot_tbl", "create_user"),
    ("evidence_snapshot_tbl", "modify_user"),
    ("judge_rule_version_lst", "create_user"),
    ("llm_usage_lst", "create_user"),
    ("prejudge_run_tbl", "create_user"),
    ("prejudge_run_tbl", "modify_user"),
    ("setting_master", "create_user"),
    ("setting_master", "modify_user"),
    ("upload_batch_tbl", "create_user"),
    ("upload_batch_tbl", "modify_user"),
)

_REDUNDANT_INDEX = "idx_attribution_tbl_feedback_source_code"


def upgrade() -> None:
    """審計欄 36 → 255（放寬不會失敗），並刪除前綴冗餘的單欄索引。

    `varchar(n)` 放寬長度上限**不觸發 table rewrite**（PG 只改 catalog 的 atttypmod），
    ACCESS EXCLUSIVE 鎖僅毫秒級；索引 DDL 則照 `db/README.md`「開／刪 index 一律加
    CONCURRENTLY」走 autocommit_block。
    """
    for table, column in _AUDIT_COLUMNS:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE varchar(255)")
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_REDUNDANT_INDEX}")


def downgrade() -> None:
    """還原為 varchar(36) 並重建索引。

    ⚠️ 收緊型別是**可能失敗**的操作：升級後若已寫入超過 36 字元的 email / `system:*` 標記，
    這裡會拋 `StringDataRightTruncation` 而整個 downgrade 交易回滾。這是刻意的——
    改成 `USING left(col, 36)` 會靜默截斷稽核欄、永久失去可追溯性，比擋下 downgrade 更糟。
    真的需要退版時，先自行決定那些列要怎麼處理。
    """
    for table, column in _AUDIT_COLUMNS:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE varchar(36)")
    with op.get_context().autocommit_block():
        op.execute(
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {_REDUNDANT_INDEX} "
            "ON attribution_tbl (feedback_source_code)"
        )
