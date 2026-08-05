"""清掉改名前的舊表與已退役表的殘骸（讓漏改的舊名 SQL fail loud）

`a8e5c31d0f62` 用 `ALTER TABLE ... RENAME TO` 改名，理論上舊名不該存在。但 dev 庫實測仍有
**17 張舊名／已退役表**（15 張全空，`attribution_history` 5 列、`judge_rule_versions` 9 列，
內容經核對全是 2026-08-05 02:06 那批測試逃逸寫進來的垃圾，`source_id='bad'` 對不上任何真實
review）——來源是重構過程中 `create_all` 型路徑與逃逸的測試執行緒重建出來的空殼。

**為什麼一定要刪，而不是放著不管**：舊名表存在時，任何漏改的舊名 SQL **不會**拋
`UndefinedTable`，只會**靜默回 0 筆**。本輪就踩到了——5 支評測腳本的原生 SQL 還寫著
`FROM attributions`，於是金標集／域路由訓練集／評測 harness 全部被建成空集合而毫無告警，
是逐檔稽核才翻出來的。刪掉之後，同類漏改會立刻炸在臉上。fail loud > fail silent。

⚠️ 同輪另以一次性 SQL 清掉 `attribution_event_lst` 裡 475 筆 `source_id='bad'` 的測試垃圾
（2026-07-14 起累積，成因是測試背景執行緒逃逸出 fixture 寫進 dev 正式庫，該成因已於
`backend/tests/conftest.py` 的 engine 定錨 + 硬守衛修掉）。那筆清理不寫進 migration——
它是 dev 環境特有的資料污染，不是 schema 演進。

不可逆，但無資料損失：全部內容都是測試垃圾（套用前已 pg_dump 至 ~/kkday-backups/）。
downgrade 不重建這些表——它們本來就不該存在，重建等於把陷阱裝回去。

Revision ID: d3b58e2c9017
Revises: f7c3a91d6b48
Create Date: 2026-08-05

"""

from collections.abc import Sequence

from alembic import op

revision: str = "d3b58e2c9017"
down_revision: str | Sequence[str] | None = "f7c3a91d6b48"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 改名前的舊表名（現行對應表見 tables.py）
_PRE_RENAME = (
    "attributions",
    "attribution_history",
    "llm_usage",
    "prejudge_runs",
    "judge_rule_versions",
    "batches",
    "settings",
    "evidence_snapshot",
    "reviews",
    "conversations",
    "freshdesk_tickets",
    "app_feedback",
    "mixpanel_tracker",
)

# 功能已整支退役、metadata 裡也不存在的孤兒表
_RETIRED = (
    "prompt_debug_reviews",
    "prompt_drafts",
    "prompt_sandbox_runs",
    "finding_notes",
)


def upgrade() -> None:
    """DROP 舊名與孤兒表（冪等；正常環境本來就沒有，只有重構過的 dev 庫有殘骸）。"""
    for name in _PRE_RENAME + _RETIRED:
        op.execute(f"DROP TABLE IF EXISTS {name} CASCADE")


def downgrade() -> None:
    """不重建——這些表的存在本身就是讓錯誤靜默化的陷阱（見 module docstring）。"""
