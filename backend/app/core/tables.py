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

# 8 面向 code（質檢彙總 prod_quality/pkg_quality 用；對齊 roster.DIM_CODE）
_DIM_CODES = (
    "positioning",
    "itinerary",
    "fee",
    "meetup",
    "redeem",
    "group_form",
    "restriction",
    "sla",
)


def _dim_columns() -> list[Column]:
    """8 面向各一欄（{code}_n INTEGER）。"""
    cols: list[Column] = []
    for code in _DIM_CODES:
        cols.append(Column(f"{code}_n", Integer, server_default="0"))
    return cols

# ── 6 表（欄位對齊舊 DDL；composite PK 用多個 primary_key）──────────────────
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
    Column("item_id", Text),
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
    # 反饋來源標記（product_reviews 拆表後，判決結果須知道 item_id 屬哪個來源表才能正確 join 回原始列）
    Column("source", Text),
    Index("idx_judgments_source", "source"),
)

# product_reviews：從 intake_items 拆出的獨立實體表（5 反饋來源中唯一已拆分者；
# 其餘 4 來源仍沿用 intake_items 通用表，見 source_registry.py 的選表邏輯）。
# PK 刻意命名 xid（非 id/oid）：避開來源自身 rec_oid / order_oid 等欄位撞名造成混淆。
product_reviews = Table(
    "product_reviews",
    metadata,
    Column("xid", BigInteger, primary_key=True, autoincrement=True),
    Column("source_record_id", Text, unique=True),  # 自然鍵：CSV 原始 rec_oid
    Column("item_id", Text, unique=True),  # 決定性生成：f"product_reviews-{rec_oid}"
    Column("member_uuid", Text),
    Column("traveller_type", Text),
    Column("lang", Text),
    Column("occurred_at", Text),  # 評論時間（排序/分頁用）
    Column("title", Text),
    Column("content", Text),  # 判決主輸入
    Column("score", Integer),
    Column("prod_oid", Text),
    Column("pkg_oid", Text),
    Column("order_oid", Text),
    Column("order_mid", Text),  # ⚠️ 會員 id（個資）
    Column("supplier_oid", Text),
    Column("product_category_main", Text),  # 商品分類主碼（如 CATEGORY_082）
    Column("product_category_sub", JSONB),  # 子分類清單
    Column("go_date", Text),  # 出發日
    Column("prod_name_snapshot", JSONB),  # 多語商品名快照（展開行用）
    Column("status", Text),
    Column("created_at", Text),
    Column("raw", Text),  # 兜底：field_map 未涵蓋的原始欄
    Index("idx_product_reviews_score", "score"),
    Index("idx_product_reviews_category_main", "product_category_main"),
    Index("idx_product_reviews_occurred_at", "occurred_at"),
    Index("idx_product_reviews_prod_oid", "prod_oid"),
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

confidence_calibration = Table(
    "confidence_calibration",
    metadata,
    Column("scope", Text, primary_key=True),
    Column("model", Text, primary_key=True),
    Column("intercept", Float),
    Column("slope", Float),
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


# ── roster 層：主檔 / 事實 / 質檢彙總（取代 sql/schema.sql 的 7 表）────────────
products = Table(
    "products",
    metadata,
    Column("prod_oid", Text, primary_key=True),
    Column("prod_mid", Text),
    Column("master_lang", Text),
    Column("prod_name", Text),
    Column("prod_summary", Text),
    Column("prod_feature", Text),
    Column("prod_desc", Text),
    Column("prod_schedules", Text),
    Column("prod_notice", Text),
    Column("prod_fee", Text),
    Column("prod_meetup", Text),
    Column("prod_redeem", Text),
    Column("prod_purchase", Text),
    Column("bd_tag_note", Text),
    Column("updated_at", Text),
)

packages = Table(
    "packages",
    metadata,
    Column("pkg_oid", Text, primary_key=True),
    Column("prod_oid", Text),
    Column("pkg_name", Text),
    Column("pkg_desc", Text),
    Column("pkg_schedules", Text),
    Column("pkg_fee", Text),
    Column("pkg_meetup", Text),
    Column("pkg_refund", Text),
    Column("pkg_order_process", Text),
    Column("updated_at", Text),
    Index("idx_packages_prod", "prod_oid"),
)

suppliers = Table(
    "suppliers",
    metadata,
    Column("supplier_oid", Text, primary_key=True),
    Column("supplier_name", Text),
    Column("updated_at", Text),
)

orders = Table(
    "orders",
    metadata,
    Column("order_oid", Text, primary_key=True),
    Column("order_mid", Text),  # ⚠️ 會員 id（個資）
    Column("prod_oid", Text),
    Column("order_profit", Float),
    Column("updated_at", Text),
    Index("idx_orders_prod", "prod_oid"),
)

inquiries = Table(
    "inquiries",
    metadata,
    Column("session_oid", Text, primary_key=True),
    Column("channel", Text),
    Column("order_oid", Text),
    Column("prod_oid", Text),
    Column("pkg_oid", Text),
    Column("supplier_oid", Text),
    Column("master_lang", Text),
    Column("zendesk_ticket_id", Text),
    Column("session_create_date", Text),
    Column("sessionable_type", Text),
    Column("sessionable_id", Text),
    Column("session_direction", Text),
    Column("msg_handler", Text),
    Column("aggregated_messages", Text),  # ⚠️ 多輪對話原文（個資）
    Column("batch_id", Text),
    Column("created_at", Text),
    Index("idx_inquiries_prod", "prod_oid"),
    Index("idx_inquiries_order", "order_oid"),
    Index("idx_inquiries_channel", "channel"),
    Index("idx_inquiries_pkg", "pkg_oid"),
    Index("idx_inquiries_supplier", "supplier_oid"),
)

prod_quality = Table(
    "prod_quality",
    metadata,
    Column("prod_oid", Text, primary_key=True),
    Column("prod_name", Text),
    Column("bd_tag_note", Text),
    Column("supplier_oid", Text),
    Column("inquiry_count", Integer, server_default="0"),
    Column("order_count", Integer, server_default="0"),
    Column("order_profit_sum", Float, server_default="0"),
    Column("judgments_total", Integer, server_default="0"),
    Column("content_issue_n", Integer, server_default="0"),
    Column("content_issue_pct", Float, server_default="0"),
    Column("contract_breach_n", Integer, server_default="0"),
    Column("top_dimension", Text),
    Column("max_confidence", Float, server_default="0"),
    Column("overall_status", Text),
    *_dim_columns(),
    Column("last_judged_at", Text),
    Index("idx_prod_quality_issue", "content_issue_n"),  # PG 可反向掃，免 DESC index
)

pkg_quality = Table(
    "pkg_quality",
    metadata,
    Column("pkg_oid", Text, primary_key=True),
    Column("prod_oid", Text),
    Column("prod_name", Text),
    Column("inquiry_count", Integer, server_default="0"),
    Column("judgments_total", Integer, server_default="0"),
    Column("content_issue_n", Integer, server_default="0"),
    Column("content_issue_pct", Float, server_default="0"),
    Column("contract_breach_n", Integer, server_default="0"),
    Column("top_dimension", Text),
    Column("max_confidence", Float, server_default="0"),
    Column("overall_status", Text),
    *_dim_columns(),
    Column("last_judged_at", Text),
    Index("idx_pkg_quality_prod", "prod_oid"),
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
