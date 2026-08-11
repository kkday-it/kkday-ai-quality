"""同步 attribution_event_lst 兩欄的註解——kind='note' 已不存於本表

備註（kind='note'）已於 b7d419e0c852 全數遷往 attribution_note_lst，本表的 kind 實測只有
prejudge / suggestion / failure / review_confirm 四種。但 `author` 與 `note_content` 兩欄的
註解仍寫著「kind=note」，語義已與現況脫節：

    author       → 現為「操作者」（人工動作事件），不限備註
    note_content → 現為「人工動作的理由文字」（correction／review_confirm／suggestion）

欄名 `note_content` 不改——改欄名要動 DTO、查詢、匯出與資料包，代價遠大於收益；
語義漂移由註解說清楚即可（註解是 schema_parity 的比對對象，故必須走 migration）。

Revision ID: b83d5f2c910a
Revises: f2a91c7b4d08
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "b83d5f2c910a"
down_revision = "f2a91c7b4d08"
branch_labels = None
depends_on = None

_A_NEW = "操作者（SSO 接入前一律 system，接入後為使用者 email）"
_A_OLD = "備註人（SSO 接入前一律 system，接入後為使用者 email；kind=note）"
_C_NEW = (
    "人工動作的理由文字（correction／review_confirm／suggestion 事件；欄名沿用歷史，語義非備註）"
)
_C_OLD = "備註內容（kind=note）"


def _set(author: str, content: str, author_old: str, content_old: str) -> None:
    op.alter_column(
        "attribution_event_lst",
        "author",
        existing_type=sa.String(255),
        comment=author,
        existing_comment=author_old,
    )
    op.alter_column(
        "attribution_event_lst",
        "note_content",
        existing_type=sa.Text(),
        comment=content,
        existing_comment=content_old,
    )


def upgrade() -> None:
    """把兩欄註解改成現況語義。"""
    _set(_A_NEW, _C_NEW, _A_OLD, _C_OLD)


def downgrade() -> None:
    """還原為舊註解。"""
    _set(_A_OLD, _C_OLD, _A_NEW, _C_NEW)
