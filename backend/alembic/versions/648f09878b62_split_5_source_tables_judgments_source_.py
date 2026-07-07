"""split 5 source tables + judgments source_id key

Revision ID: 648f09878b62
Revises: 8ff0b8cfc8f2
Create Date: 2026-07-03

5 反饋來源各自拆成獨立表（對齊源 schema、PK=特徵 id），judgments 改用 (source, source_id) 關聯，
移除 item_id 複合鍵 + intake_items 通用表。資料以 raw JSON（原始源列，key=源欄名）忠實重建。
downgrade 不支援（結構性大改）——回滾請用 pg_dump 備份還原。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "648f09878b62"
down_revision: str | Sequence[str] | None = "8ff0b8cfc8f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 各來源表欄位 → raw JSON key（多數同名；mixpanel $ / 大寫欄名淨化，故 (表欄, raw鍵) 成對）
_CONV = [
    (c, c)
    for c in [
        "session_oid",
        "zendesk_ticket_id",
        "session_create_date",
        "order_oid",
        "order_mid",
        "sessionable_type",
        "sessionable_id",
        "prod_oid",
        "session_direction",
        "supplier_oid",
        "msg_handler",
        "aggregated_messages",
        "prod_bd_tag_note",
        "prod_name_zh_tw",
        "order_profit",
    ]
]
_FRESH = [
    (c, c)
    for c in [
        "id",
        "display_id",
        "ticket_type",
        "subject",
        "description",
        "notes",
        "attachments",
        "st_survey_rating",
        "product_id",
        "custom_field",
        "tags",
        "status_name",
        "priority_name",
        "source_name",
        "created_at",
        "updated_at",
        "requester_id",
        "parent_ticket_id",
    ]
]
_APPF = [
    (c, c)
    for c in ["oid", "created_datetime", "comment", "score", "source", "lang_code", "version"]
]
_MIX = [
    ("event", "event"),
    ("time", "time"),
    ("insert_id", "$insert_id"),
    ("distinct_id", "$distinct_id"),
    ("feedback_signal", "feedback_signal"),
    ("negative_items", "negative_items"),
    ("display_style", "display_style"),
    ("order_mid", "order_mid"),
    ("order_status", "order_status"),
    ("order_master_mid", "order_master_mid"),
    ("is_marketplace", "is_marketplace"),
    ("prod_mid", "prod_mid"),
    ("pkg_oid", "pkg_oid"),
    ("prod_city_code", "prod_city_code"),
    ("prod_country_code", "prod_country_code"),
    ("prod_info", "prod_info"),
    ("bd_tag", "bd_tag"),
    ("msg_handler", "msg_handler"),
    ("current_url", "$current_url"),
    ("platform", "Platform"),
    ("mp_country_code", "mp_country_code"),
    ("os", "$os"),
]
_PR = [
    (c, c)
    for c in [
        "rec_oid",
        "member_uuid",
        "create_date",
        "rec_title",
        "rec_desc",
        "rec_scores",
        "traveller_type",
        "lang_code",
        "prod_oid",
        "pkg_oid",
        "order_oid",
        "order_mid",
        "supplier_oid",
        "order_snap_json",
        "lst_dt_go",
        "product_category",
    ]
]

# 4 未拆表來源：(source code, 表名, 欄位對, PK 表欄, PK raw鍵)
_SOURCES = [
    ("conversations", "conversations", _CONV, "session_oid", "session_oid"),
    ("freshdesk_tickets", "freshdesk_tickets", _FRESH, "id", "id"),
    ("app_feedback", "app_feedback", _APPF, "oid", "oid"),
    ("mixpanel_tracker", "mixpanel_tracker", _MIX, "insert_id", "$insert_id"),
]


def _insert_sql(
    table: str, src_table: str, pairs, pk_col: str, pk_raw: str, where_src: str | None
) -> str:
    """組 `INSERT INTO table (cols) SELECT raw::jsonb->>'key'... FROM src [WHERE source=X] ON CONFLICT DO NOTHING`。"""
    cols = ", ".join(f'"{tc}"' for tc, _ in pairs)
    sel = ", ".join(f"raw::jsonb->>'{rk}'" for _, rk in pairs)
    src_filter = f"AND source='{where_src}'" if where_src else ""
    return (
        f'INSERT INTO "{table}" ({cols}) '
        f"SELECT {sel} FROM {src_table} "
        f"WHERE raw IS NOT NULL AND raw <> '{{}}' AND raw::jsonb->>'{pk_raw}' IS NOT NULL {src_filter} "
        f'ON CONFLICT ("{pk_col}") DO NOTHING'
    )


def upgrade() -> None:
    from app.core import tables as T

    bind = op.get_bind()

    # 0. presale_postsale 舊名收斂（冪等）
    op.execute("UPDATE intake_items SET source='conversations' WHERE source='presale_postsale'")
    op.execute("UPDATE judgments SET source='conversations' WHERE source='presale_postsale'")

    # 1. 建 4 新來源表（從 metadata）
    for t in (T.conversations, T.freshdesk_tickets, T.app_feedback, T.mixpanel_tracker):
        t.create(bind, checkfirst=True)

    # 2. 4 來源：intake_items.raw（原始源列 JSON）→ 各新表
    for source, table, pairs, pk_col, pk_raw in _SOURCES:
        op.execute(_insert_sql(table, "intake_items", pairs, pk_col, pk_raw, where_src=source))

    # 3. product_reviews 重建（改 16 源欄 rec_oid PK）：舊表改名 → 建新 → 從舊 raw 重灌 → 刪舊
    op.rename_table("product_reviews", "product_reviews_old")
    # 改名不會改索引名 → 舊索引仍叫 idx_product_reviews_* 會與新表撞名，先刪（IF EXISTS 容錯）
    for _idx in (
        "idx_product_reviews_score",
        "idx_product_reviews_category_main",
        "idx_product_reviews_occurred_at",
        "idx_product_reviews_prod_oid",
    ):
        op.execute(f'DROP INDEX IF EXISTS "{_idx}"')
    T.product_reviews.create(bind, checkfirst=True)
    op.execute(
        _insert_sql(
            "product_reviews", "product_reviews_old", _PR, "rec_oid", "rec_oid", where_src=None
        )
    )
    op.drop_table("product_reviews_old")

    # 4. judgments 換關聯鍵：加 source_id → re-key → 換索引 → 刪 item_id
    op.add_column("judgments", sa.Column("source_id", sa.Text(), nullable=True))
    # product_reviews：item_id=`product_reviews-{rec_oid}` → source_id=rec_oid（strip 前綴）
    op.execute(
        "UPDATE judgments SET source_id = regexp_replace(item_id, '^product_reviews-', '') WHERE source='product_reviews'"
    )
    # 其餘來源：item_id 為雜湊，無法反推 → join intake_items 由 raw 取特徵 id 原值（趁 intake 未刪）
    for source, _table, _pairs, _pk_col, pk_raw in _SOURCES:
        op.execute(
            f"UPDATE judgments j SET source_id = ii.raw::jsonb->>'{pk_raw}' "
            f"FROM intake_items ii WHERE j.item_id = ii.item_id AND j.source='{source}'"
        )
    op.create_index("idx_judgments_source_id", "judgments", ["source", "source_id"], unique=False)
    op.drop_index("idx_judgments_item_id", table_name="judgments")
    op.drop_column("judgments", "item_id")

    # 5. 刪 intake_items（含 manual；資料已遷移或棄用）
    op.drop_table("intake_items")


def downgrade() -> None:
    """結構性大改，不支援自動回滾——請用 pg_dump 備份還原（backend/backups/kkdb_ai_quality_pre_5table_*.sql）。"""
    raise NotImplementedError("5-table split is not reversible; restore from pg_dump backup")
