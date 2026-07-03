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
    # item_id 是所有歸因查詢（list_problems / overview / breakdown / unjudged）與 intake/專表
    # outerjoin 的鍵；缺此索引時對 8 萬列做 nested-loop/seq-scan → 列表與縱覽載入緩慢。加索引消除瓶頸。
    Index("idx_judgments_item_id", "item_id"),
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
    # ── 多歸因判決（product_reviews 專表自帶判決欄；其餘 4 來源仍走 judgments 表）──
    # 一則評論可同時違反多規則（如 C-1-1 + C-2-1），各違規線隔離存為 judges 陣列一元素，
    # 各自帶 action / 負責單位；取代「一評論一 finding」的 judgments 1:1 舊模型（見 ReviewJudge）。
    Column("judges", JSONB, nullable=False, server_default=text("'[]'::jsonb")),  # list[ReviewJudge]
    Column("review_polarity", Text),  # 整則評論傾向（一次判定，非逐違規）；NULL=未判
    Column("judged_at", Text),  # 最近一次判決時間（ISO）；NULL=未判
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
