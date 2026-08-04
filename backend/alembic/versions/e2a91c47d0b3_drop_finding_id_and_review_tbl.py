"""退場 attribution_tbl.finding_id 與 prompt_debug_review_tbl

**finding_id**：實測 6,242 列 →(feedback_source_code, source_id, l1_code, l2_code) 有 6,242 個
相異組合、**零重複**，證明它就是那四欄的字串編碼（`fd_{source}_{source_id}__{l1}__{l2}`），100% 冗餘。
更糟的是**其中 5,704 列（91%）的編碼與實際欄位互相矛盾**——前綴是 2026-07-01 改名前的舊 source code
`product_reviews`，而 `feedback_source_code` 已是 `reviews`。那個值還顯示在「歸因詳情」抽屜給人看。

身分職責由 `attribution_oid`（serial PK）承擔；唯一鍵改掛真正的自然鍵四欄——規範第一章明訂
unique key 可為多欄位組合（`idx_table_name_unique??`）。app 層原本用 `finding_id IS NULL` 判斷
「該來源列是否已初判」，改用 `attribution_oid IS NULL`，語義完全等價。

**prompt_debug_review_tbl**：人工評判案例改存前端本地（Pinia + localStorage）——案例是個人調試用的
暫存語料而非團隊共享資產。改寫／回歸端點改由請求整包帶上案例內容（`PromptDebugCaseIn`），
後端純運算不持久化；原本掛在 POST 端點上的兩條契約驗證（欄名須在契約內、同欄不得既標對又標錯）
隨之搬進該 model 的 validator——那裡是案例進入後端的唯一入口。

⚠️ 不可逆：finding_id 的字串值與案例庫內容永久消失（套用前已 pg_dump 至 ~/kkday-backups/）。
downgrade 只重建結構，不還原資料。

Revision ID: e2a91c47d0b3
Revises: c4e8a1f6b092
Create Date: 2026-08-04

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e2a91c47d0b3"
down_revision: str | Sequence[str] | None = "c4e8a1f6b092"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UPGRADE: tuple[str, ...] = (
    # 唯一鍵從「四欄的字串編碼」改掛「那四個真實欄位」
    "DROP INDEX IF EXISTS idx_attribution_tbl_unique01",
    "CREATE UNIQUE INDEX idx_attribution_tbl_unique01 ON attribution_tbl "
    "(feedback_source_code, source_id, l1_code, l2_code)",
    "ALTER TABLE attribution_tbl DROP COLUMN IF EXISTS finding_id",
    # 人工評判案例庫改存前端本地
    "DROP INDEX IF EXISTS idx_prompt_debug_review_tbl_create_date",
    "DROP TABLE IF EXISTS prompt_debug_review_tbl",
)


def upgrade() -> None:
    """唯一鍵改掛自然鍵四欄 → 刪 finding_id；刪案例庫表。"""
    for stmt in _UPGRADE:
        op.execute(stmt)


def downgrade() -> None:
    """重建結構（不還原資料）。"""
    op.add_column("attribution_tbl", sa.Column("finding_id", sa.Text(), nullable=True))
    op.execute("DROP INDEX IF EXISTS idx_attribution_tbl_unique01")
    op.execute("CREATE UNIQUE INDEX idx_attribution_tbl_unique01 ON attribution_tbl (finding_id)")
    op.create_table(
        "prompt_debug_review_tbl",
        sa.Column("prompt_debug_review_oid", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("conversation", sa.Text(), nullable=False),
        sa.Column("ai_output", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "corrections",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "confirmed",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("reviewer_comment", sa.Text(), server_default="", nullable=False),
        sa.Column("prompt_version", sa.Text(), server_default="", nullable=False),
        sa.Column("model", sa.Text(), server_default="", nullable=False),
        sa.Column("create_user", sa.String(36), server_default="", nullable=False),
        sa.Column(
            "create_date",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("modify_user", sa.String(36), nullable=True),
        sa.Column("modify_date", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("prompt_debug_review_oid"),
    )
    op.create_index(
        "idx_prompt_debug_review_tbl_create_date", "prompt_debug_review_tbl", ["create_date"]
    )
