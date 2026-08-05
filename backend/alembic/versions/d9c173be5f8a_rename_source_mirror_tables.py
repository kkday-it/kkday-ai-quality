"""5 張來源鏡像表：表名對齊 DDL 規範 + 補表註解（**欄位與型別一律不動**）

使用者 2026-08-04 提問點出的缺口：豁免申請的理由是「欄名與型別要忠實鏡射上游 BQ 取數輸出」，
但**表名是我們自己取的、與上游零耦合**——不在豁免理由涵蓋範圍內，該照規範改。

本支只動三件事，全部是 metadata 層：
1. 表名 → `名詞_tbl`（與 `attribution_tbl` 同軸：一列＝一筆反饋、以自然鍵 upsert 的主要資料表）
2. PK 約束名 → `<新表名>_pkey`（順帶清掉 `reviews` 那個 `product_reviews_pkey1` 的陳年殘留）
3. 補 `COMMENT ON TABLE`

**刻意不動**（這才是豁免的實質內容）：
· 欄名與型別——逐欄對齊上游取數 SQL 的輸出契約，改了就失去「拿匯入檔逐欄對照」的能力
· PK 仍為自然鍵（rec_oid / session_oid / id / oid / insert_id），不換 serial `<名詞>_oid`
· 不補審計四欄——這些列由上傳匯入產生，建立者/修改者語義落在 `upload_batch_tbl`

因此 5 表在「表名 / 表註解 / 索引命名」三項達成規範，其餘維持豁免並於申請中載明。

Revision ID: d9c173be5f8a
Revises: b6f04a2e7d31
Create Date: 2026-08-04

"""

from collections.abc import Sequence

from alembic import op

revision: str = "d9c173be5f8a"
down_revision: str | Sequence[str] | None = "b6f04a2e7d31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UPGRADE: tuple[str, ...] = (
    "ALTER TABLE reviews RENAME TO review_tbl",
    "DO $$ DECLARE c text; BEGIN SELECT conname INTO c FROM pg_constraint WHERE conrelid = 'review_tbl'::regclass AND contype = 'p'; EXECUTE format('ALTER TABLE review_tbl RENAME CONSTRAINT %I TO review_tbl_pkey', c); END $$;",
    "COMMENT ON TABLE review_tbl IS '商品評論來源鏡像（忠實鏡射上游 BQ 取數輸出：欄名與型別逐欄對齊，不做 canonical 化）。一列＝一則評論，PK rec_oid'",
    "ALTER TABLE conversations RENAME TO conversation_tbl",
    "DO $$ DECLARE c text; BEGIN SELECT conname INTO c FROM pg_constraint WHERE conrelid = 'conversation_tbl'::regclass AND contype = 'p'; EXECUTE format('ALTER TABLE conversation_tbl RENAME CONSTRAINT %I TO conversation_tbl_pkey', c); END $$;",
    "COMMENT ON TABLE conversation_tbl IS '售前售後進線來源鏡像（忠實鏡射上游 BQ 取數輸出）。一列＝一個 IM session，PK session_oid'",
    "ALTER TABLE freshdesk_tickets RENAME TO freshdesk_ticket_tbl",
    "DO $$ DECLARE c text; BEGIN SELECT conname INTO c FROM pg_constraint WHERE conrelid = 'freshdesk_ticket_tbl'::regclass AND contype = 'p'; EXECUTE format('ALTER TABLE freshdesk_ticket_tbl RENAME CONSTRAINT %I TO freshdesk_ticket_tbl_pkey', c); END $$;",
    "COMMENT ON TABLE freshdesk_ticket_tbl IS 'Freshdesk 工單來源鏡像（忠實鏡射上游取數輸出）。一列＝一張工單，PK id'",
    "ALTER TABLE app_feedback RENAME TO app_feedback_tbl",
    "DO $$ DECLARE c text; BEGIN SELECT conname INTO c FROM pg_constraint WHERE conrelid = 'app_feedback_tbl'::regclass AND contype = 'p'; EXECUTE format('ALTER TABLE app_feedback_tbl RENAME CONSTRAINT %I TO app_feedback_tbl_pkey', c); END $$;",
    "COMMENT ON TABLE app_feedback_tbl IS 'App 內回饋來源鏡像（忠實鏡射上游取數輸出）。一列＝一則回饋，PK oid'",
    "ALTER TABLE mixpanel_tracker RENAME TO mixpanel_tracker_tbl",
    "DO $$ DECLARE c text; BEGIN SELECT conname INTO c FROM pg_constraint WHERE conrelid = 'mixpanel_tracker_tbl'::regclass AND contype = 'p'; EXECUTE format('ALTER TABLE mixpanel_tracker_tbl RENAME CONSTRAINT %I TO mixpanel_tracker_tbl_pkey', c); END $$;",
    "COMMENT ON TABLE mixpanel_tracker_tbl IS 'Mixpanel 埋點回饋來源鏡像（忠實鏡射上游取數輸出）。一列＝一個事件，PK insert_id'",
    # ── 索引改名 ──
    "ALTER INDEX idx_reviews_create_date RENAME TO idx_review_tbl_create_date",
    "ALTER INDEX idx_reviews_prod_oid RENAME TO idx_review_tbl_prod_oid",
    "ALTER INDEX idx_conversations_bucket RENAME TO idx_conversation_tbl_bucket",
    "ALTER INDEX idx_conversations_inbound_time RENAME TO idx_conversation_tbl_inbound_time",
    "ALTER INDEX idx_conversations_prod_oid RENAME TO idx_conversation_tbl_prod_oid",
    "ALTER INDEX idx_conversations_vertical RENAME TO idx_conversation_tbl_vertical",
    "ALTER INDEX idx_freshdesk_created_at RENAME TO idx_freshdesk_ticket_tbl_created_at",
    "ALTER INDEX idx_freshdesk_product_id RENAME TO idx_freshdesk_ticket_tbl_product_id",
    "ALTER INDEX idx_app_feedback_created RENAME TO idx_app_feedback_tbl_created",
    "ALTER INDEX idx_mixpanel_time RENAME TO idx_mixpanel_tracker_tbl_time",
)

_TABLE_RENAMES: tuple[tuple[str, str], ...] = (
    ("reviews", "review_tbl"),
    ("conversations", "conversation_tbl"),
    ("freshdesk_tickets", "freshdesk_ticket_tbl"),
    ("app_feedback", "app_feedback_tbl"),
    ("mixpanel_tracker", "mixpanel_tracker_tbl"),
)


def upgrade() -> None:
    """表名 / PK 約束名 / 索引名對齊規範，並補表註解。欄位完全不動。"""
    for stmt in _UPGRADE:
        op.execute(stmt)


_DOWNGRADE_INDEXES: tuple[tuple[str, str], ...] = (
    ("idx_review_tbl_create_date", "idx_reviews_create_date"),
    ("idx_review_tbl_prod_oid", "idx_reviews_prod_oid"),
    ("idx_conversation_tbl_bucket", "idx_conversations_bucket"),
    ("idx_conversation_tbl_inbound_time", "idx_conversations_inbound_time"),
    ("idx_conversation_tbl_prod_oid", "idx_conversations_prod_oid"),
    ("idx_conversation_tbl_vertical", "idx_conversations_vertical"),
    ("idx_freshdesk_ticket_tbl_created_at", "idx_freshdesk_created_at"),
    ("idx_freshdesk_ticket_tbl_product_id", "idx_freshdesk_product_id"),
    ("idx_app_feedback_tbl_created", "idx_app_feedback_created"),
    ("idx_mixpanel_tracker_tbl_time", "idx_mixpanel_time"),
)


def downgrade() -> None:
    """表名 / PK 約束名 / 索引名一併改回。

    索引名**必須**還原：baseline v2 的 downgrade 是以原名逐一 `drop_index`，留著新名會讓
    整條 downgrade 鏈斷在最後一支。PK 約束名改回 `<原表名>_pkey`——本機 dev 庫那個
    `product_reviews_pkey1` 的陳年殘留不還原（upgrade 的解析是動態的，不影響再次 upgrade）。
    """
    for new, old in _DOWNGRADE_INDEXES:
        op.execute(f"ALTER INDEX {new} RENAME TO {old}")
    for old, new in _TABLE_RENAMES:
        op.execute(
            f"DO $$ DECLARE c text; BEGIN "
            f"SELECT conname INTO c FROM pg_constraint "
            f"WHERE conrelid = '{new}'::regclass AND contype = 'p'; "
            f"EXECUTE format('ALTER TABLE {new} RENAME CONSTRAINT %I TO {old}_pkey', c); END $$;"
        )
        op.execute(f"ALTER TABLE {new} RENAME TO {old}")
