"""資料層 schema 與 engine（SQLAlchemy Core · PostgreSQL only）。

app 操作庫一律 PostgreSQL（對齊 QC DB）；連線取自 `config.env.database_url`
（dev 預設本機 `postgresql+psycopg2://localhost:5432/kkdb_ai_quality`，prod 經 env 覆蓋）。
db.py 的 21 個函式皆走本模組的 engine + Table metadata；schema 演進由 Alembic 管（見 alembic/）。

時間欄位沿用 ISO 字串（Text，與既有 API 回傳形態一致）。
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as _pg_insert
from sqlalchemy.engine import Engine

from app.core.config import env

metadata = MetaData()

# ── 來源數據表（欄位對齊舊 DDL；composite PK 用多個 primary_key）──────────────────
intake_items = Table(
    "intake_items",
    metadata,
    Column("item_id", Text, primary_key=True),
    Column("source", Text),
    Column("batch_id", Text),
    Column("prod_oid", Text),
    Column("pkg_oid", Text),
    Column("rating", Integer),
    Column("comment", Text),
    Column("raw", Text),
    Column("status", Text),
    Column("created_at", Text),
    Column("occurred_at", Text),  # 原始事件時間（評論 create_date 等）；伺服器端分頁排序鍵
)

judgments = Table(
    "judgments",
    metadata,
    Column("finding_id", Text, primary_key=True),
    # ── 來源複合鍵 (source, source_id)：關聯回來源表（source 定表、source_id 對該表特徵 id）──
    # 取代舊 item_id 複合字串（`{source}-{natural_id}`）。source_id 存特徵 id 原值
    # （product_reviews→rec_oid / conversations→session_oid / freshdesk_tickets→id /
    #  app_feedback→oid / mixpanel_tracker→insert_id）。
    Column("source", Text),
    Column("source_id", Text),
    Column("prod_oid", Text),
    Column("pkg_oid", Text),
    Column("dimension", Text),
    Column("confidence", Float),
    Column("raw_confidence", Float),
    Column("is_enhanced", Integer, server_default="0"),
    Column("enhance_model", Text),
    Column("needs_review", Integer, server_default="0"),
    Column("true_label", Text),
    Column("suspected_field", Text),
    Column("recommended_action", Text),
    Column("data", Text),
    Column("status", Text),
    Column("created_at", Text),
    Index("idx_judgments_source", "source"),
    # (source, source_id) 複合索引：所有歸因查詢的 join / EXISTS 走此複合條件（取代舊 idx_judgments_item_id）
    Index("idx_judgments_source_id", "source", "source_id"),
)

# ── 5 反饋來源獨立實體表（各自對齊源表 schema，PK=特徵 id；欄位存原始源值 raw text）─────
# 統一經 source_registry（table + natural_key）+ config/ai_judge/source_mapping.json（源欄→canonical）
# 產出顯示層 canonical 欄（content/score/occurred_at…）。欄位一律 Text（忠實 raw；巢狀 JSON 於
# _enrich 端解析，如 product_reviews.order_snap_json → prod_name）。
product_reviews = Table(
    "product_reviews",
    metadata,
    Column("rec_oid", Text, primary_key=True),  # 特徵 id
    Column("member_uuid", Text),
    Column("create_date", Text),  # canonical occurred_at
    Column("rec_title", Text),  # canonical title
    Column("rec_desc", Text),  # canonical content（判決主輸入）
    Column("rec_scores", Text),  # canonical score
    Column("traveller_type", Text),
    Column("lang_code", Text),  # canonical lang
    Column("prod_oid", Text),
    Column("pkg_oid", Text),
    Column("order_oid", Text),
    Column("order_mid", Text),  # ⚠️ 會員 id（個資）
    Column("supplier_oid", Text),
    Column("order_snap_json", Text),  # 多語商品名快照 JSON（enrich 解析 prod_name/package_name）
    Column("lst_dt_go", Text),  # canonical go_date（出發日）
    Column("product_category", Text),  # 商品分類（enrich 解析 main/sub）
    Index("idx_product_reviews_create_date", "create_date"),
    Index("idx_product_reviews_prod_oid", "prod_oid"),
    Index("idx_product_reviews_product_category", "product_category"),
)

conversations = Table(
    "conversations",
    metadata,
    Column("session_oid", Text, primary_key=True),  # 特徵 id
    Column("zendesk_ticket_id", Text),
    Column("session_create_date", Text),  # canonical occurred_at
    Column("order_oid", Text),
    Column("order_mid", Text),
    Column("sessionable_type", Text),  # canonical channel
    Column("sessionable_id", Text),
    Column("prod_oid", Text),
    Column("session_direction", Text),
    Column("supplier_oid", Text),
    Column("msg_handler", Text),
    Column("aggregated_messages", Text),  # canonical content
    Column("prod_bd_tag_note", Text),
    Column("prod_name_zh_tw", Text),
    Column("order_profit", Text),
    Index("idx_conversations_create_date", "session_create_date"),
    Index("idx_conversations_prod_oid", "prod_oid"),
)

freshdesk_tickets = Table(
    "freshdesk_tickets",
    metadata,
    Column("id", Text, primary_key=True),  # 特徵 id
    Column("display_id", Text),
    Column("ticket_type", Text),
    Column("subject", Text),  # canonical title
    Column("description", Text),  # canonical content
    Column("notes", Text),
    Column("attachments", Text),
    Column("st_survey_rating", Text),  # canonical score
    Column("product_id", Text),  # canonical prod_oid
    Column("custom_field", Text),
    Column("tags", Text),
    Column("status_name", Text),
    Column("priority_name", Text),
    Column("source_name", Text),  # canonical channel
    Column("created_at", Text),  # canonical occurred_at
    Column("updated_at", Text),
    Column("requester_id", Text),
    Column("parent_ticket_id", Text),
    Index("idx_freshdesk_created_at", "created_at"),
    Index("idx_freshdesk_product_id", "product_id"),
)

app_feedback = Table(
    "app_feedback",
    metadata,
    Column("oid", Text, primary_key=True),  # 特徵 id
    Column("created_datetime", Text),  # canonical occurred_at
    Column("comment", Text),  # canonical content
    Column("score", Text),  # canonical score
    Column("source", Text),  # 來源渠道（app 端，與 judgments.source 不同語意）
    Column("lang_code", Text),  # canonical lang
    Column("version", Text),
    Index("idx_app_feedback_created", "created_datetime"),
)

mixpanel_tracker = Table(
    "mixpanel_tracker",
    metadata,
    Column("insert_id", Text, primary_key=True),  # 特徵 id（源 $insert_id 淨化）
    Column("event", Text),  # canonical channel
    Column("time", Text),  # canonical occurred_at
    Column("distinct_id", Text),  # 源 $distinct_id 淨化
    Column("feedback_signal", Text),
    Column("negative_items", Text),  # canonical content
    Column("display_style", Text),
    Column("order_mid", Text),
    Column("order_status", Text),
    Column("order_master_mid", Text),
    Column("is_marketplace", Text),
    Column("prod_mid", Text),  # canonical prod_oid
    Column("pkg_oid", Text),
    Column("prod_city_code", Text),
    Column("prod_country_code", Text),
    Column("prod_info", Text),
    Column("bd_tag", Text),
    Column("msg_handler", Text),
    Column("current_url", Text),  # 源 $current_url 淨化
    Column("platform", Text),  # 源 Platform 淨化
    Column("mp_country_code", Text),
    Column("os", Text),  # 源 $os 淨化
    Index("idx_mixpanel_time", "time"),
)

batches = Table(
    "batches",
    metadata,
    Column("batch_id", Text, primary_key=True),
    Column("name", Text),
    Column("source", Text),
    Column("original_name", Text),
    Column("row_count", Integer),
    Column("inserted_count", Integer),
    Column("uploaded_at", Text),
)

users = Table(
    "users",
    metadata,
    Column("user_id", Text, primary_key=True),
    Column("email", Text, unique=True),
    Column("password_hash", Text),
    Column("created_at", Text),
)

user_settings = Table(
    "user_settings",
    metadata,
    Column("user_id", Text, primary_key=True),
    Column("data", Text),
    Column("updated_at", Text),
)

# ── 判決規則版本（config/ai_judge/ 的 7 rule + schema 的 live + 歷史）────────────
# append-only 快照：每次存檔 insert 新版本列（不就地改），規避 JSONB write-amplification。
# 檔案 config/ai_judge/rule_C-*.json 為默認 seed；DB 存 live + 完整歷史；一 rule_code 僅一 active。
judge_rule_versions = Table(
    "judge_rule_versions",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("rule_code", Text, nullable=False),  # 'C-1'..'C-7' | 'schema'
    Column("version", Integer, nullable=False),  # per rule_code 遞增
    Column("content", JSONB, nullable=False),  # 完整 rule/schema JSON
    Column("note", Text),
    Column("author", Text),  # user_id
    Column("is_active", Boolean, nullable=False, server_default="false"),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    UniqueConstraint("rule_code", "version", name="uq_judge_rule_code_version"),
    # 一 rule_code 僅一 active（部分唯一索引）
    Index(
        "uq_judge_rule_active",
        "rule_code",
        unique=True,
        postgresql_where=text("is_active"),
    ),
)


# ── engine（lazy；可由測試 set_engine 換成測試庫）───────────────────────────
_engine: Engine | None = None


def resolve_url() -> str:
    """生效的 SQLAlchemy URL（PostgreSQL；取自 config.env.database_url）。"""
    return env.database_url


def get_engine() -> Engine:
    """取當前 engine（首次依 resolve_url 建立）。db.py 一律經此取連線。"""
    global _engine
    if _engine is None:
        _engine = create_engine(resolve_url(), future=True)
    return _engine


def set_engine(url: str) -> Engine:
    """重設 engine（測試指向測試庫 / 切換連線用）。"""
    global _engine
    _engine = create_engine(url, future=True)
    return _engine


def upsert(table: Table, values: dict, pk: list[str]):
    """INSERT … ON CONFLICT(pk) DO UPDATE（PostgreSQL；取代舊 sqlite INSERT OR REPLACE）。

    Args:
        table: 目標 Table。
        values: 欲寫入的欄位值 map。
        pk: 衝突鍵欄位名（單一或 composite）。

    Returns:
        可執行的 upsert statement。
    """
    # 只更新 values 內提供的欄位（minus pk）；未提供者保留既有，不被 NULL 覆蓋。
    stmt = _pg_insert(table).values(**values)
    update = {k: stmt.excluded[k] for k in values if k not in pk}
    return stmt.on_conflict_do_update(index_elements=pk, set_=update)


def upsert_ignore(table: Table, values: dict, pk: list[str]):
    """INSERT … ON CONFLICT(pk) DO NOTHING（取代 sqlite INSERT OR IGNORE）。"""
    return _pg_insert(table).values(**values).on_conflict_do_nothing(index_elements=pk)


def upsert_preserve(table: Table, values: dict, pk: list[str], preserve_if_empty: list[str]):
    """upsert；preserve_if_empty 欄位若新值為空字串則保留既有值（只更新 values 內欄位）。

    等價 sqlite `COALESCE(NULLIF(excluded.col,''), table.col)`：即時撈/CSV 補灌時，
    既有非空內容不被空值覆蓋（prod_name 等）。其餘有提供的欄位以新值覆蓋。
    """
    from sqlalchemy import func as _func

    stmt = _pg_insert(table).values(**values)
    update = {}
    for k in values:
        if k in pk:
            continue
        if k in preserve_if_empty:
            update[k] = _func.coalesce(_func.nullif(stmt.excluded[k], ""), table.c[k])
        else:
            update[k] = stmt.excluded[k]
    return stmt.on_conflict_do_update(index_elements=pk, set_=update)
