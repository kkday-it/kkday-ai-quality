"""對齊 5 個與 tables.py 漂移的欄註解

`test_schema_parity` 原本比對欄位／欄序／索引／約束，**結構上不比對註解**——而本輪的主要
交付物之一就是 13 表 + 126 欄的 COMMENT。於是註解漂了也沒有任何紅燈，實測漂了 5 欄：

**① 3 個「ISO 8601 字串」已經不成立**（`attribution_tbl.create_date` /
`upload_batch_tbl.create_date` / `setting_master.modify_date`）：這些欄在同一輪就從 text
轉成了 `timestamptz`，註解卻停在轉型前的描述，等於告訴讀者一件已經不真的事。

**② 2 個 label 欄是我自己在兩處寫了不同文字**（`attribution_tbl.l1_label` / `l2_label`）：
`c8a3e71f0b64` 的 `COMMENT ON` 與 `tables.py` 的 `comment=` 描述同一件事卻用了不同措辭。
兩邊都對，但不一致——SSOT 是 `tables.py`，故以它為準。

同輪已把註解比對加進 `test_schema_parity`（`_COMMENT_SQL`），此後這類漂移會立刻紅燈。

Revision ID: c2d70b9e4f81
Revises: e9f1c04a7b23
Create Date: 2026-08-05

"""

from collections.abc import Sequence

from alembic import op

revision: str = "c2d70b9e4f81"
down_revision: str | Sequence[str] | None = "e9f1c04a7b23"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UPGRADE: tuple[str, ...] = (
    "COMMENT ON COLUMN attribution_tbl.create_date IS "
    "'初判落庫時間（timestamptz，UTC）＝本列唯一時間源'",
    "COMMENT ON COLUMN attribution_tbl.l1_label IS 'L1 域中文名（判決當下的快照，非讀取時推導）'",
    "COMMENT ON COLUMN attribution_tbl.l2_label IS "
    "'L2 面向中文名（判決當下的快照，理由同 l1_label）'",
    "COMMENT ON COLUMN upload_batch_tbl.create_date IS '上傳時間（timestamptz，UTC）'",
    "COMMENT ON COLUMN setting_master.modify_date IS '最後更新時間（timestamptz，UTC）'",
)


def upgrade() -> None:
    """把 DB 註解對齊 tables.py（catalog-only，不阻塞讀寫、完全可逆）。"""
    for stmt in _UPGRADE:
        op.execute(stmt)


def downgrade() -> None:
    """還原成漂移前的文字（僅為鏈完整性；那些描述本身是錯的，不建議停在這裡）。"""
    for stmt in (
        "COMMENT ON COLUMN attribution_tbl.create_date IS "
        "'初判落庫時間（ISO 8601 字串）＝本列唯一時間源'",
        "COMMENT ON COLUMN attribution_tbl.l1_label IS "
        "'L1 域名稱（判決當下的快照，非讀取時推導——分類體系改寫措辭時不回溯改變歷史歸因的顯示）'",
        "COMMENT ON COLUMN attribution_tbl.l2_label IS 'L2 面向名稱（判決當下的快照，理由同 l1_label）'",
        "COMMENT ON COLUMN upload_batch_tbl.create_date IS '上傳時間（ISO 8601 字串，含時區偏移）'",
        "COMMENT ON COLUMN setting_master.modify_date IS '最後更新時間（ISO 8601 字串）'",
    ):
        op.execute(stmt)
