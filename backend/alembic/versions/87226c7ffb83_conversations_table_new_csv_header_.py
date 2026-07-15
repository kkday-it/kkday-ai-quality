"""conversations table new csv header schema

Revision ID: 87226c7ffb83
Revises: 124748246b38
Create Date: 2026-07-15

售前售後進線來源匯出格式改版：aggregated_messages 拆為 chatbot_conversation/human_conversation
(source_mapping.merge_fields 併回 canonical content)，sessionable_type→conversation_type，
session_create_date→session_datetime_tw，sessionable_id 無替代欄故直接刪除。舊資料已備份
backend/../backups/pre_conversations_schema_migration_20260715.sql（data-only）。
downgrade 僅還原欄位結構，不還原資料（回滾請用備份）。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "87226c7ffb83"
down_revision: str | Sequence[str] | None = "124748246b38"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_COLUMNS = [
    "session_date_tw",
    "session_datetime_tw",
    "order_lang",
    "order_price_pay",
    "order_create_source_code",
    "product_name",
    "product_category",
    "order_go_date",
    "product_timezone",
    "trip_stage",
    "order_status",
    "supplier_name",
    "review_score",
    "review_content",
    "cs_task_type_name",
    "inbound_session_count",
    "conversation_type",
    "user_msg_count",
    "agent_msg_count",
    "chatbot_conversation",
    "human_conversation",
]

_DROPPED_COLUMNS = [
    "session_create_date",
    "sessionable_type",
    "sessionable_id",
    "aggregated_messages",
]


def upgrade() -> None:
    """conversations 表改對齊新版 CSV 表頭：加新欄、刪舊欄、換日期索引。"""
    op.drop_index("idx_conversations_create_date", table_name="conversations")
    for col in _NEW_COLUMNS:
        op.add_column("conversations", sa.Column(col, sa.Text(), nullable=True))
    for col in _DROPPED_COLUMNS:
        op.drop_column("conversations", col)
    op.create_index(
        "idx_conversations_datetime", "conversations", ["session_datetime_tw"], unique=False
    )


def downgrade() -> None:
    """僅還原欄位結構，不還原資料——回滾請用 backups/pre_conversations_schema_migration_20260715.sql。"""
    op.drop_index("idx_conversations_datetime", table_name="conversations")
    for col in _NEW_COLUMNS:
        op.drop_column("conversations", col)
    for col in _DROPPED_COLUMNS:
        op.add_column("conversations", sa.Column(col, sa.Text(), nullable=True))
    op.create_index(
        "idx_conversations_create_date", "conversations", ["session_create_date"], unique=False
    )
