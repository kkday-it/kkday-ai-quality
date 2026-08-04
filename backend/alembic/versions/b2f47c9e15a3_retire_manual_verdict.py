"""退役人工判決軸：attributions 三欄 → is_auto_accepted；刪 finding_notes 表

實測依據（2026-08-04 dev 庫）：6,242 條歸因中 `verdict_status='confirmed'` 僅 1 筆、
`dismissed` 0 筆，其餘全是 `auto_confirmed`(5,471) 或 `new`(770)——人工確認／忽略實質沒人用，
只有系統自動確認在跑。`verdict_by` 只有 `system:auto_confirm`(3,318) 與 NULL(2,924) 兩種值
＝零資訊量；`verdict_at` 在人工判決退役後無產生者。

保留的資訊恰好是一個 bit：「系統有沒有自動採納」→ 收斂成 `is_auto_accepted boolean`，
資訊零損失，且符合 DDL 規範「flag 欄用 `is_` 開頭且型別 boolean」。

`finding_notes` 一併退役：8 列中 6 列已是孤兒（`finding_id` 無對應 attributions），內容全是
`111111`/`aaaa` 這類測試字串；備註能力保留在 `attribution_history` 的 `kind='note'` 事件——
後者綁 `(source, source_id)` 這個**跨重新初判穩定**的鍵，不像 finding 級會斷鏈。

⚠️ 不可逆：`verdict_status` 的 confirmed/dismissed 語義在轉成 boolean 後無法還原（實際只有 1 筆），
`finding_notes` 的內容永久消失。downgrade 只把結構加回（nullable），不還原資料。

Revision ID: b2f47c9e15a3
Revises: 94e60400715b
Create Date: 2026-08-04

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b2f47c9e15a3"
down_revision: str | Sequence[str] | None = "94e60400715b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """判決三欄 → is_auto_accepted；刪 finding_notes；清 kind='verdict' 事件與殭屍 run 列。"""
    # ① 新欄先建（nullable + default false），再由舊值換算，最後才刪舊欄——順序反了會丟資料
    # 冪等（IF NOT EXISTS / IF EXISTS）：entrypoint 的 `adopt` 路徑代表「被 create_all 建成
    # head schema、卻蓋章在舊 revision」的庫真實存在，此時本支會對已存在的欄位動刀。
    op.execute(
        "ALTER TABLE attributions ADD COLUMN IF NOT EXISTS is_auto_accepted BOOLEAN DEFAULT false"
    )
    # ⚠️ 換算必須走 `DO $$` 動態 SQL：PG 在**解析階段**就驗證欄位存在，靜態 UPDATE 即使加了
    # `WHERE EXISTS (information_schema…)` 也會在舊欄不存在時直接拋 UndefinedColumn
    # （WHERE 是執行期，解析在那之前）。EXECUTE 讓語句只在欄位真的存在時才被解析。
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'attributions' AND column_name = 'verdict_status'
            ) THEN
                EXECUTE 'UPDATE attributions '
                        'SET is_auto_accepted = (verdict_status = ''auto_confirmed'')';
            END IF;
        END $$;
    """)
    for col in ("verdict_status", "verdict_by", "verdict_at"):
        op.execute(f"ALTER TABLE attributions DROP COLUMN IF EXISTS {col}")

    # ② finding 級備註表退場（評論級 kind='note' 事件保留）
    op.execute("DROP INDEX IF EXISTS idx_finding_notes_finding")
    op.execute("DROP TABLE IF EXISTS finding_notes")

    # ③ 判決轉移事件：寫入路徑已移除，殘留列（dev 實測 2 筆）一併清掉，
    #    否則前端時間軸會渲染出永遠不會再產生的事件型別
    op.execute("DELETE FROM attribution_history WHERE kind = 'verdict'")

    # ④ 順帶清掉重啟遺留的殭屍 run（`mark_running_interrupted` 過去只改 in-mem 不回寫 DB，
    #    dev 實測留下 3 筆停在 2026-07-11 / 07-14 的 running/cancelling 列；修復已於同輪落地）
    op.execute(
        "UPDATE prejudge_runs SET status = 'interrupted' "
        "WHERE status IN ('running', 'cancelling') AND finished_at IS NULL"
    )


def downgrade() -> None:
    """僅還原結構（nullable），**不還原資料**——判決語義與備註內容已不可逆地遺失。"""
    op.add_column("attributions", sa.Column("verdict_status", sa.Text(), nullable=True))
    op.add_column("attributions", sa.Column("verdict_by", sa.Text(), nullable=True))
    op.add_column("attributions", sa.Column("verdict_at", sa.Text(), nullable=True))
    op.execute(
        "UPDATE attributions SET verdict_status = "
        "CASE WHEN is_auto_accepted THEN 'auto_confirmed' ELSE 'new' END"
    )
    op.drop_column("attributions", "is_auto_accepted")

    op.create_table(
        "finding_notes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("finding_id", sa.Text(), nullable=False),
        sa.Column("author", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_finding_notes_finding", "finding_notes", ["finding_id"])
