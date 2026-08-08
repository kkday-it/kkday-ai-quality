"""自然鍵改 DEFERRABLE 唯一約束 + 複審確認納入人工託管的索引謂詞

本 migration 做兩件相關但獨立的事，都圍繞 `attribution_tbl`。

## 一、`idx_attribution_tbl_unique01`：unique index → DEFERRABLE unique constraint

**動機**：兩條歸因要互換 L1/L2 面向時，「先改 A 再改 B」與「先改 B 再改 A」都會在中途撞上
這組自然鍵。在此之前使用者只能手動走三步（先把其中一條改成第三個暫時面向），中間態是假資料。
延後到 commit 才檢查之後，互換就是單一交易內兩次 UPDATE（見 `db.corrections.swap_attribution_slots`）。

**為什麼是約束不是索引**：PG 的 deferrable 唯一性只能掛在 constraint 上，掛不到裸的
`CREATE UNIQUE INDEX`。轉換後 PG 仍會建一條同名 backing index，`pg_indexes.indexdef` 與轉換前
**逐字相同**（實測），故 `test_schema_parity` 的索引比對不受影響。

**刻意的副作用**：deferrable 約束不能當 `ON CONFLICT` 的 arbiter
（`ERROR: ON CONFLICT does not support deferrable unique constraints ... as arbiters`），
所以本表從此無法 upsert。這正是我們要的——`replace_source_findings` 早就是「整組刪除後重插」
而非逐筆 upsert（其 docstring 明寫「否則舊面向殘留孤兒列」，並聲明本索引「不必為此讓步」）。
唯一還在對本表 upsert 的是 `insert_finding`，而它零 production 呼叫端，隨本輪一併退役。

## 二、`idx_attribution_tbl_mix02` 的 partial 謂詞補上 `review_status = 'confirmed'`

「確認 AI 判對了」現在也算人工介入（`_shared.human_touched_cond()` 加了第四個 OR 分支）——
在此之前 `confirm_attribution` 只寫 `review_status='confirmed'`、不設任何託管旗標，於是該反饋
仍是 AI 託管，下次重新初判整組 DELETE，複審記錄隨列消失（複審做完等於白做，且無任何錯誤訊息）。

⚠️ 索引謂詞必須跟著改：partial 索引只在「查詢條件蘊含索引謂詞」時才被採用。查詢多了一個 OR
分支之後就不再蘊含舊謂詞，索引會**靜默失效退回 seq scan**——結果不會錯，但
`problems.py` 那句「走 idx_attribution_tbl_mix02 partial 索引」的註解會變成假的。

Revision ID: a3e58d21c9f4
Revises: f4c62a9b17e0
Create Date: 2026-08-07
"""

from alembic import op

revision = "a3e58d21c9f4"
down_revision = "f4c62a9b17e0"
branch_labels = None
depends_on = None

_MIX02_OLD = "is_manual_created OR is_human_corrected OR is_deleted"
_MIX02_NEW = "is_manual_created OR is_human_corrected OR is_deleted OR review_status = 'confirmed'"


def upgrade() -> None:
    # ── 一、自然鍵轉 DEFERRABLE 約束 ──────────────────────────────────────────
    # DROP 再 ADD（而非 ADD CONSTRAINT ... USING INDEX）：USING INDEX 會把既有索引「收編」成
    # 約束的 backing index，但**不能同時指定 DEFERRABLE**（PG 限制），所以只能重建。
    # 本表 6,321 列，重建成本可忽略。
    op.execute("DROP INDEX IF EXISTS idx_attribution_tbl_unique01")
    op.execute(
        "ALTER TABLE attribution_tbl ADD CONSTRAINT idx_attribution_tbl_unique01 "
        "UNIQUE (feedback_source_code, source_id, l1_code, l2_code) "
        "DEFERRABLE INITIALLY IMMEDIATE"
    )

    # ── 二、mix02 partial 謂詞對齊 human_touched_cond() ────────────────────────
    op.execute("DROP INDEX IF EXISTS idx_attribution_tbl_mix02")
    op.execute(
        "CREATE INDEX idx_attribution_tbl_mix02 ON attribution_tbl "
        f"(feedback_source_code, source_id) WHERE {_MIX02_NEW}"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_attribution_tbl_mix02")
    op.execute(
        "CREATE INDEX idx_attribution_tbl_mix02 ON attribution_tbl "
        f"(feedback_source_code, source_id) WHERE {_MIX02_OLD}"
    )

    op.execute("ALTER TABLE attribution_tbl DROP CONSTRAINT IF EXISTS idx_attribution_tbl_unique01")
    op.execute(
        "CREATE UNIQUE INDEX idx_attribution_tbl_unique01 ON attribution_tbl "
        "(feedback_source_code, source_id, l1_code, l2_code)"
    )
