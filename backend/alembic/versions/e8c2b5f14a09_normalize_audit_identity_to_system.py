"""稽核身分統一為 system：沒有 SSO 就沒有經過驗證的身分，記 email 是假的可追溯性

本專案 2026-07-22 已「去帳戶系統」（無 register/login/切換帳號），`permissions.json` 目前
`no_auth_grant_all: true`——代表**沒有任何身分是被驗證過的**。在這種狀態下把身分欄記成
`local@kkday.internal`（本地佔位假信箱）或使用者自填的 email，稽核價值是零卻看起來像真的，
比誠實記 `system` 更糟。`judge_rule_version_lst` 本來就有 9 列在用 `system`，本次扶正為全庫慣例。

**改動範圍**：所有身分欄（`create_user` / `modify_user` / `author`）**有值的**一律改寫為
`system`；`NULL` 維持 `NULL`——缺值代表「該情境下這個欄位沒有語義」（如 `author` 只有
kind='note' 的備註事件才有人、`modify_user` 為 NULL 代表從未被修改過），不是「身分不明」。

⚠️ **不可逆**：被覆寫的歷史值（含去帳戶系統之前、帳號系統仍在時記下的真實 email）無法從
本 migration 還原，downgrade 是 no-op。此為 2026-08-06 明確決策：SSO 接入前一律視為系統操作。
be2 SSO 接入後 `auth_verifiers` 會回真實 email，屆時新資料自然帶回真人身分，不需回滾本次。

Revision ID: e8c2b5f14a09
Revises: d4b19c7a2e50
Create Date: 2026-08-06

"""

from collections.abc import Sequence

from alembic import op

revision: str = "e8c2b5f14a09"
down_revision: str | Sequence[str] | None = "d4b19c7a2e50"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (表, 欄)：涵蓋 information_schema 掃出的全部 15 個身分欄，一個都不漏才叫「統一」。
_IDENTITY_COLS: tuple[tuple[str, str], ...] = (
    ("attribution_event_lst", "create_user"),
    ("attribution_event_lst", "author"),
    ("attribution_tbl", "create_user"),
    ("attribution_tbl", "modify_user"),
    ("evidence_snapshot_tbl", "create_user"),
    ("evidence_snapshot_tbl", "modify_user"),
    ("judge_rule_version_lst", "create_user"),
    ("llm_usage_lst", "create_user"),
    ("prejudge_run_log_lst", "create_user"),
    ("prejudge_run_tbl", "create_user"),
    ("prejudge_run_tbl", "modify_user"),
    ("setting_master", "create_user"),
    ("setting_master", "modify_user"),
    ("upload_batch_tbl", "create_user"),
    ("upload_batch_tbl", "modify_user"),
)


# 欄註解同步：舊註解寫「user email 或 system:* 標記」，與新語義漂了。本 repo 的
# `test_schema_parity` 會比對註解（migration 鏈 vs create_all），故兩邊必須一起改。
_IDENTITY_COMMENTS: tuple[str, ...] = (
    "COMMENT ON COLUMN attribution_event_lst.create_user IS '觸發人（SSO 接入前一律 system，接入後為使用者 email；kind=prejudge）'",
    "COMMENT ON COLUMN attribution_event_lst.author IS '備註人（SSO 接入前一律 system，接入後為使用者 email；kind=note）'",
    "COMMENT ON COLUMN attribution_tbl.create_user IS '建立者（SSO 接入前一律 system，接入後為使用者 email）'",
    "COMMENT ON COLUMN attribution_tbl.modify_user IS '最後修改者（SSO 接入前一律 system，接入後為使用者 email；NULL＝從未修改）'",
    "COMMENT ON COLUMN evidence_snapshot_tbl.create_user IS '建立者（SSO 接入前一律 system，接入後為使用者 email）'",
    "COMMENT ON COLUMN evidence_snapshot_tbl.modify_user IS '最後修改者（SSO 接入前一律 system，接入後為使用者 email；NULL＝從未修改）'",
    "COMMENT ON COLUMN judge_rule_version_lst.create_user IS '存檔人（SSO 接入前一律 system，接入後為使用者 email）'",
    "COMMENT ON COLUMN llm_usage_lst.create_user IS '建立者（SSO 接入前一律 system，接入後為使用者 email）'",
    "COMMENT ON COLUMN prejudge_run_log_lst.create_user IS '觸發人（SSO 接入前一律 system，接入後為使用者 email）'",
    "COMMENT ON COLUMN prejudge_run_tbl.create_user IS '觸發人（SSO 接入前一律 system，接入後為使用者 email）'",
    "COMMENT ON COLUMN prejudge_run_tbl.modify_user IS '最後修改者（SSO 接入前一律 system，接入後為使用者 email；NULL＝從未修改）'",
    "COMMENT ON COLUMN setting_master.create_user IS '建立者（SSO 接入前一律 system，接入後為使用者 email）'",
    "COMMENT ON COLUMN setting_master.modify_user IS '最後修改者（SSO 接入前一律 system，接入後為使用者 email；NULL＝從未修改）'",
    "COMMENT ON COLUMN upload_batch_tbl.create_user IS '建立者（SSO 接入前一律 system，接入後為使用者 email）'",
    "COMMENT ON COLUMN upload_batch_tbl.modify_user IS '最後修改者（SSO 接入前一律 system，接入後為使用者 email；NULL＝從未修改）'",
)


def upgrade() -> None:
    """有值的身分欄一律改寫為 'system'（NULL 不動，空字串視同有值一併正規化）+ 同步欄註解。"""
    for stmt in _IDENTITY_COMMENTS:
        op.execute(stmt)
    for table, col in _IDENTITY_COLS:
        op.execute(
            f"UPDATE {table} SET {col} = 'system' "  # noqa: S608  表/欄名來自本檔常數，非外部輸入
            f"WHERE {col} IS NOT NULL AND {col} <> 'system'"
        )


def downgrade() -> None:
    """no-op：原始身分值已被覆寫，本 migration 沒有保留它們，無從還原（見檔頭 ⚠️）。"""
