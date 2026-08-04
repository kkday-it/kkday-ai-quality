"""刪除零讀取欄位與死資料列（attributions / llm_usage / batches / settings / rule_versions）

實測依據（2026-08-04 dev 庫，逐欄查全 codebase 讀取點）：

- `attributions.prod_oid`：6,242/6,242 有值，**零讀取**。唯一的 prod_oid 篩選走
  `_shared._apply_source_filters`，作用在**來源表**（reviews/conversations 自己的 prod_oid），
  與本欄無關；註解宣稱的 ProductDetail 下鑽頁面已不存在。
- `attributions.needs_review`：100% 由 `conf_tier` 決定（實測交叉表零例外：
  auto_accept×false 5,750／jury×false 360／needs_review×true 132），而 `conf_tier` 本就在 wire 上。
  出 wire 的 `needs_review` 鍵前端零消費（前端所有 needs_review 字樣都是 tier 的**值**）。
- `llm_usage.provider` / `source_id`：只出現在 `_INSERT_COLS`（寫入白名單），
  無任何聚合／查詢投影它們——寫進去就再也沒讀出來過。
- `batches.inserted_count`：UI 不顯示，且 16/16 恆等 `row_count`；
  回填函式 `update_batch_inserted()` 零呼叫端。逐塊實際落庫筆數仍在上傳 job 的
  記憶體快照裡（`_bump_sheet` 的 `inserted`），可觀測性不受影響。
- `settings.data` 的 `overview_boards` / `active_overview_board_id`：前端消費者已於
  commit c4936eb 移除，DB 實測值為 `[]` / `null`，後端只剩一整套 sanitize plumbing 在空轉。
- `judge_rule_versions` 的 `product_vertical` 列（id=37, is_active=true）：該規則 2026-07-27
  已被 `bd_tag_vertical` 取代並全棧退役（後者現有 3 版、1 個 active），這列是孤兒。

⚠️ 不可逆：欄位資料永久消失。downgrade 只把結構加回（nullable），不還原資料——
`needs_review` 可由 `conf_tier` 完全重算，其餘無從還原。

Revision ID: c7a41e0d9b82
Revises: b2f47c9e15a3
Create Date: 2026-08-04

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c7a41e0d9b82"
down_revision: str | Sequence[str] | None = "b2f47c9e15a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 表 → 該表要退場的欄位（全部 IF EXISTS，對 entrypoint 的 adopt 路徑冪等）
_DROP_COLUMNS: dict[str, tuple[str, ...]] = {
    "attributions": ("prod_oid", "needs_review"),
    "llm_usage": ("provider", "source_id"),
    "batches": ("inserted_count",),
}


def upgrade() -> None:
    """刪 5 個零讀取欄位；清 settings 的兩個死 JSON key 與 product_vertical 孤兒列。"""
    for table, cols in _DROP_COLUMNS.items():
        for col in cols:
            op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {col}")

    # settings.data 是 text 欄（存 JSON 字串），故經 jsonb 往返再寫回字串
    op.execute(
        "UPDATE settings SET data = ("
        "  (data::jsonb) - 'overview_boards' - 'active_overview_board_id'"
        ")::text "
        "WHERE data::jsonb ?| array['overview_boards', 'active_overview_board_id']"
    )

    op.execute("DELETE FROM judge_rule_versions WHERE rule_code = 'product_vertical'")


def downgrade() -> None:
    """僅還原欄位結構（nullable）；needs_review 可由 conf_tier 重算，其餘資料不還原。"""
    op.add_column("attributions", sa.Column("prod_oid", sa.Text(), nullable=True))
    op.add_column(
        "attributions",
        sa.Column("needs_review", sa.Boolean(), server_default="false", nullable=True),
    )
    op.execute("UPDATE attributions SET needs_review = (conf_tier = 'needs_review')")

    op.add_column("llm_usage", sa.Column("provider", sa.Text(), nullable=True))
    op.add_column("llm_usage", sa.Column("source_id", sa.Text(), nullable=True))
    op.add_column("batches", sa.Column("inserted_count", sa.Integer(), nullable=True))
