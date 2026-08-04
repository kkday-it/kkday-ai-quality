"""退役 Prompt 沙盒：drop prompt_sandbox_runs / prompt_drafts，清 settings 的 sandbox 綁定

沙盒（歸因列表 › 工具列「初判 Prompt 測試」）與其草稿閉環於 2026-08-04 全棧退役。
連帶退場的還有草稿編輯／採納抽屜、`/judge-rules` 的 draft CRUD 與 dry-run validate 端點、
`prompt_eval` 的沙盒執行函式（`sandbox_classify` / `domain_verdicts` 診斷 overlay）。

實測依據（2026-08-04 dev 庫）：
- `prompt_sandbox_runs` 13 列，最後寫入 2026-07-17（18 天前），全部 `triggered_by` 為同一位
  開發者、`versions` 欄 0/13 使用——單機試跑歷史，非團隊生產資產。無任何 FK 進出
  （`pg_constraint` 0 rows），亦無依賴的 view / rule（`pg_depend`+`pg_rewrite` 0 rows）。
- `prompt_drafts` **0 列**——草稿是「編輯中的暫存內容」，採納入庫後即刪，本就不長期存在。

⚠️ **必須順手清 `settings.data.llm_area_configs` 的 `"sandbox"` 鍵**，否則是一顆延遲地雷：
綁定寫入端 `settings._validate_area_configs` 會對整份 mapping 逐 key 校驗
（`if area_key not in LLM_AREAS: raise ValueError`），而前端 `setAreaConfig` 送的是**整份**
map。一旦 `"sandbox"` 從 `LLM_AREAS` 移除卻殘留在 DB，使用者改**初判**的模型配置就會被
這個 stale 鍵連累成 400、永遠存不進去。讀取端不做剪除，所以只有寫入路徑會炸——本機 dev
的 `llm_area_configs` 目前是 `{prejudge, prompt_debug}`（無 sandbox 鍵）故不會發作，但任何曾在
沙盒面板選過配置的環境即刻中招，因此這裡無條件清。

⚠️ 不可逆：兩張表的資料永久消失（已 pg_dump 至 ~/kkday-backups/）。downgrade 只重建空表結構。

Revision ID: e5b83c214f7d
Revises: c7a41e0d9b82
Create Date: 2026-08-04

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e5b83c214f7d"
down_revision: str | Sequence[str] | None = "c7a41e0d9b82"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """drop 兩張沙盒表；清 settings 殘留的 sandbox 功能區綁定。"""
    op.execute("DROP INDEX IF EXISTS idx_prompt_sandbox_runs_created")
    op.execute("DROP TABLE IF EXISTS prompt_sandbox_runs")
    op.execute("DROP TABLE IF EXISTS prompt_drafts")

    # settings.data 是 text 欄（存 JSON 字串）→ 經 jsonb 往返再寫回字串。
    # `#-` 走路徑刪除（llm_area_configs 是巢狀物件，`-` 只能刪頂層鍵）。
    op.execute(
        "UPDATE settings "
        "SET data = ((data::jsonb) #- '{llm_area_configs,sandbox}')::text "
        "WHERE (data::jsonb) -> 'llm_area_configs' ? 'sandbox'"
    )


def downgrade() -> None:
    """重建兩張空表（結構還原，資料不還原）。"""
    op.create_table(
        "prompt_drafts",
        sa.Column("rule_code", sa.Text(), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("base_version", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.Text(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.PrimaryKeyConstraint("rule_code"),
    )
    op.create_table(
        "prompt_sandbox_runs",
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("item_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("prompt_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("results", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("log", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("triggered_by", sa.Text(), nullable=True),
        sa.Column("job_id", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "versions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "drafts",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("compare", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index("idx_prompt_sandbox_runs_created", "prompt_sandbox_runs", ["created_at"])
