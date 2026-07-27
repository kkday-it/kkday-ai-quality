"""conversations_30col_reshape

Revision ID: 830a12b92fff
Revises: 4ac23d6d20b4
Create Date: 2026-07-24 09:53:16.718425

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "830a12b92fff"
down_revision: str | Sequence[str] | None = "4ac23d6d20b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """conversations 整表重塑為新 30 欄 schema（上游 SQL 改版，新舊欄集幾乎不重疊，DROP+CREATE 而非
    逐欄 ALTER）。⚠️ 執行前若表內仍有既存資料須自行 pg_dump 備份（本次落地時實測仍有 73,836 筆
    舊格式資料，非空表，已備份）。
    """
    op.execute("DROP TABLE IF EXISTS conversations CASCADE")
    op.create_table(
        "conversations",
        sa.Column("session_oid", sa.Text(), nullable=False),
        sa.Column("bucket", sa.Text(), nullable=True),
        sa.Column("inbound_time", sa.Text(), nullable=True),
        sa.Column("trip_stage", sa.Text(), nullable=True),
        sa.Column("godate_diff", sa.Text(), nullable=True),
        sa.Column("msg_handler_bucket", sa.Text(), nullable=True),
        sa.Column("member_uuid", sa.Text(), nullable=True),
        sa.Column("order_oid", sa.Text(), nullable=True),
        sa.Column("order_mid", sa.Text(), nullable=True),
        sa.Column("order_create_time", sa.Text(), nullable=True),
        sa.Column("order_status_now", sa.Text(), nullable=True),
        sa.Column("order_lang", sa.Text(), nullable=True),
        sa.Column("go_date", sa.Text(), nullable=True),
        sa.Column("order_price", sa.Text(), nullable=True),
        sa.Column("order_profit", sa.Text(), nullable=True),
        sa.Column("order_create_source_code", sa.Text(), nullable=True),
        sa.Column("prod_oid", sa.Text(), nullable=True),
        sa.Column("product_name", sa.Text(), nullable=True),
        sa.Column("product_tz", sa.Text(), nullable=True),
        sa.Column("vertical", sa.Text(), nullable=True),
        sa.Column("bd_tag_cd", sa.Text(), nullable=True),
        sa.Column("bd_tag", sa.Text(), nullable=True),
        sa.Column("PM", sa.Text(), nullable=True),
        sa.Column("product_category", sa.Text(), nullable=True),
        sa.Column("supplier_oid", sa.Text(), nullable=True),
        sa.Column("supplier_name", sa.Text(), nullable=True),
        sa.Column("cs_tag_oid", sa.Text(), nullable=True),
        sa.Column("cs_tag_name", sa.Text(), nullable=True),
        sa.Column("user_message_count", sa.Text(), nullable=True),
        sa.Column("conversation_full", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("session_oid"),
    )
    op.create_index("idx_conversations_inbound_time", "conversations", ["inbound_time"])
    op.create_index("idx_conversations_prod_oid", "conversations", ["prod_oid"])
    op.create_index("idx_conversations_bucket", "conversations", ["bucket"])
    op.create_index("idx_conversations_vertical", "conversations", ["vertical"])
    op.create_index("idx_conversations_product_category", "conversations", ["product_category"])


def downgrade() -> None:
    """重建舊（2026-07-15 第二代）conversations schema 結構；不還原資料。"""
    op.execute("DROP TABLE IF EXISTS conversations CASCADE")
    op.create_table(
        "conversations",
        sa.Column("session_oid", sa.Text(), nullable=False),
        sa.Column("zendesk_ticket_id", sa.Text(), nullable=True),
        sa.Column("session_date_tw", sa.Text(), nullable=True),
        sa.Column("session_datetime_tw", sa.Text(), nullable=True),
        sa.Column("order_mid", sa.Text(), nullable=True),
        sa.Column("order_oid", sa.Text(), nullable=True),
        sa.Column("order_lang", sa.Text(), nullable=True),
        sa.Column("order_price_pay", sa.Text(), nullable=True),
        sa.Column("order_profit", sa.Text(), nullable=True),
        sa.Column("order_create_source_code", sa.Text(), nullable=True),
        sa.Column("prod_oid", sa.Text(), nullable=True),
        sa.Column("product_name", sa.Text(), nullable=True),
        sa.Column("prod_name_zh_tw", sa.Text(), nullable=True),
        sa.Column("prod_bd_tag_note", sa.Text(), nullable=True),
        sa.Column("product_category", sa.Text(), nullable=True),
        sa.Column("order_go_date", sa.Text(), nullable=True),
        sa.Column("product_timezone", sa.Text(), nullable=True),
        sa.Column("trip_stage", sa.Text(), nullable=True),
        sa.Column("order_status", sa.Text(), nullable=True),
        sa.Column("supplier_oid", sa.Text(), nullable=True),
        sa.Column("supplier_name", sa.Text(), nullable=True),
        sa.Column("msg_handler", sa.Text(), nullable=True),
        sa.Column("review_score", sa.Text(), nullable=True),
        sa.Column("review_content", sa.Text(), nullable=True),
        sa.Column("cs_task_type_name", sa.Text(), nullable=True),
        sa.Column("inbound_session_count", sa.Text(), nullable=True),
        sa.Column("conversation_type", sa.Text(), nullable=True),
        sa.Column("user_msg_count", sa.Text(), nullable=True),
        sa.Column("agent_msg_count", sa.Text(), nullable=True),
        sa.Column("chatbot_conversation", sa.Text(), nullable=True),
        sa.Column("human_conversation", sa.Text(), nullable=True),
        sa.Column("session_direction", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("session_oid"),
    )
    op.create_index("idx_conversations_datetime", "conversations", ["session_datetime_tw"])
    op.create_index("idx_conversations_prod_oid", "conversations", ["prod_oid"])
