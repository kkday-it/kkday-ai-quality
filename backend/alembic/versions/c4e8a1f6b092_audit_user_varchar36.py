"""審計欄 create_user 型別對齊規範 varchar(36)

規範第二章明訂 `create_user, create_date, modify_user, modify_date` 的型別為 `varchar(36)`, `timestamp`。
`a8e5c31d0f62` 用 `ADD COLUMN` 新建的審計欄都給了 `varchar(36)`，但**由既有欄改名而來**的四個
（`triggered_by`／`author`／`reviewer` → `create_user`）沿用了原本的 `text`，漏改型別。

安全性：套用前實測四張表的 `create_user` 最長值為 36 字元（`judge_rule_version_lst` 的
`system:conversations-30col-migration`），**零筆超過**，轉型不會截斷。

⚠️ `review_tbl.create_date` 雖也是 `text` 但**刻意不動**——那是評論本身的建立時間（鏡射上游 BQ 取數
輸出），只是欄名恰好與審計欄同字，不屬審計欄語義。

Revision ID: c4e8a1f6b092
Revises: d9c173be5f8a
Create Date: 2026-08-04

"""

from collections.abc import Sequence

from alembic import op

revision: str = "c4e8a1f6b092"
down_revision: str | Sequence[str] | None = "d9c173be5f8a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UPGRADE: tuple[str, ...] = (
    "ALTER TABLE attribution_event_lst ALTER COLUMN create_user TYPE varchar(36)",
    "ALTER TABLE judge_rule_version_lst ALTER COLUMN create_user TYPE varchar(36)",
    "ALTER TABLE prejudge_run_tbl ALTER COLUMN create_user TYPE varchar(36)",
    "ALTER TABLE prompt_debug_review_tbl ALTER COLUMN create_user TYPE varchar(36)",
    # ALTER TYPE 不會改寫既有 DEFAULT 的型別標註（仍是 ''::text），與 create_all 產出的
    # ''::character varying 不一致 → schema_parity 會抓到漂移。顯式重設讓兩條路徑一致。
    "ALTER TABLE prompt_debug_review_tbl ALTER COLUMN create_user SET DEFAULT ''::character varying",
)

_DOWNGRADE: tuple[str, ...] = (
    "ALTER TABLE attribution_event_lst ALTER COLUMN create_user TYPE text",
    "ALTER TABLE judge_rule_version_lst ALTER COLUMN create_user TYPE text",
    "ALTER TABLE prejudge_run_tbl ALTER COLUMN create_user TYPE text",
    "ALTER TABLE prompt_debug_review_tbl ALTER COLUMN create_user TYPE text",
)


def upgrade() -> None:
    """四個由改名而來的 create_user：text → varchar(36)。"""
    for stmt in _UPGRADE:
        op.execute(stmt)


def downgrade() -> None:
    """還原為 text（放寬長度，不會失敗）。"""
    for stmt in _DOWNGRADE:
        op.execute(stmt)
