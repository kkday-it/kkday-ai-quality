-- AI 質檢本地 DB schema（kkdb_ai_quality.db）— 主檔 + 事實 + 質檢彙總
--
-- 全 DB 共 10 表（無前綴、語義化命名）：
--   主檔   products / packages / suppliers
--   事實   orders / inquiries
--   質檢   judgments（判定明細，建於 app/core/db.py）+ prod_quality / pkg_quality（彙總）
--   錄入   intake_items / batches（建於 app/core/db.py）
-- 本檔負責：products / packages / suppliers / orders / inquiries / prod_quality / pkg_quality
--
-- 來源映射（已驗證 SQL 見 docs/PLAN-V3-售前售後進線.md 附錄 A，project=kkday-data-dap）：
--   inquiries ← dw_kkdb_imassage.session/channel/session_mapping + dw_kkdb.message(_session) + dw_kkdb_chatbot.chatbot_messages
--   orders    ← dw_kkdb.order_lst(prod_oid) + dm_tableau.tableau_ec_index(名稱/利潤/bd_tag, 排除B2D)
--   products  ← dw_kkdb_product.*（Sheet TourProductPackageAnalysis 為其 sample dump）
--   packages  ← dw_kkdb_product.*（pkg 欄位）
--   suppliers ← dw_kkdb.supplier
--   質檢表（judgments/prod_quality/pkg_quality）無數倉來源（自產）；回寫公司須 DAP 另開 dataset
--
-- 導入：sqlite3 backend/data/kkdb_ai_quality.db < backend/sql/schema.sql
-- 灌資料：cd backend && .venv/bin/python -m app.core.roster
-- BigQuery 移植：TEXT→STRING / REAL→FLOAT64 / INTEGER→INT64，ANSI DDL 零改寫。

-- ═══════════════ 主檔 ═══════════════

-- 商品（prod_oid 去重）。內容欄以 Sheet/dw_kkdb_product 為主，CSV 僅補 name/bd_tag。
CREATE TABLE IF NOT EXISTS products (
    prod_oid       TEXT PRIMARY KEY,
    prod_mid       TEXT,
    master_lang    TEXT,
    prod_name      TEXT,
    prod_summary   TEXT,
    prod_feature   TEXT,
    prod_desc      TEXT,
    prod_schedules TEXT,      -- 行程流程 PMDL_SCHEDULE（JSON）
    prod_notice    TEXT,      -- 限制與風險 PMDL_NOTICE.cust_reminds（JSON）
    prod_fee       TEXT,      -- 費用資訊 PMDL_INC_NINC（JSON：include/not_include）
    prod_meetup    TEXT,      -- 集合資訊 PMDL_VENUE_LOCATION + PMDL_TOUR.meetup_time（JSON）
    prod_redeem    TEXT,      -- 使用兌換 PMDL_EXCHANGE_LOCATION/_VALID（JSON）
    prod_purchase  TEXT,      -- 購買須知 PMDL_PURCHASE_SUMMARY.summary
    bd_tag_note    TEXT,
    updated_at     TEXT
    -- 成團條件 / 承諾與SLA 不在 description_module（系統/config 欄位，另接來源）
);

-- 方案（pkg_oid 去重，FK→商品）。一商品多方案；內容來自進線 merged CSV 的 packages_json。
CREATE TABLE IF NOT EXISTS packages (
    pkg_oid           TEXT PRIMARY KEY,
    prod_oid          TEXT,
    pkg_name          TEXT,
    pkg_desc          TEXT,       -- PMDL_PACKAGE_DESC（JSON）
    pkg_schedules     TEXT,       -- 行程 PMDL_SCHEDULE（JSON）
    pkg_fee           TEXT,       -- 費用 PMDL_INC_NINC（JSON）
    pkg_meetup        TEXT,       -- 集合 PMDL_VENUE_LOCATION（JSON）
    pkg_refund        TEXT,       -- 退款SLA PMDL_REFUND_POLICY（JSON）
    pkg_order_process TEXT,       -- 成團 order_process_setting（JSON config）
    updated_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_packages_prod ON packages(prod_oid);

-- 供應商（supplier_oid 去重）。name 待 dw_kkdb.supplier 補。
CREATE TABLE IF NOT EXISTS suppliers (
    supplier_oid  TEXT PRIMARY KEY,
    supplier_name TEXT,
    updated_at    TEXT
);

-- ═══════════════ 事實 ═══════════════

-- 訂單（order_oid 去重；order_mid 1:1 會員，order_profit 訂單級）。
CREATE TABLE IF NOT EXISTS orders (
    order_oid    TEXT PRIMARY KEY,
    order_mid    TEXT,        -- ⚠️ 會員 id（個資）
    prod_oid     TEXT,
    order_profit REAL,
    updated_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_orders_prod ON orders(prod_oid);

-- 進線訊息（session_oid 去重，事實 grain）。⚠️ aggregated_messages 含個資。
CREATE TABLE IF NOT EXISTS inquiries (
    session_oid         TEXT PRIMARY KEY,
    channel             TEXT,   -- presale | postsale
    order_oid           TEXT,
    prod_oid            TEXT,
    pkg_oid             TEXT,   -- 進線綁定方案（order_message 來自 message_session；chatbot 為空）
    supplier_oid        TEXT,
    master_lang         TEXT,   -- 商品主語系（zh-tw/ja/en…）
    zendesk_ticket_id   TEXT,
    session_create_date TEXT,
    sessionable_type    TEXT,   -- order_message | chatbot
    sessionable_id      TEXT,
    session_direction   TEXT,   -- IN | OUT（chatbot 為空）
    msg_handler         TEXT,   -- KKDAY | supplier
    aggregated_messages TEXT,   -- ⚠️ 多輪對話原文（個資 + L2 判決主輸入）
    batch_id            TEXT,
    created_at          TEXT
);
-- idx_inquiries_pkg / idx_inquiries_supplier 於 roster._migrate 建（依賴 ALTER 後的新欄位）
CREATE INDEX IF NOT EXISTS idx_inquiries_prod ON inquiries(prod_oid);
CREATE INDEX IF NOT EXISTS idx_inquiries_order ON inquiries(order_oid);
CREATE INDEX IF NOT EXISTS idx_inquiries_channel ON inquiries(channel);

-- ═══════════════ 質檢彙總（物化 rollup，判定後 batch 重建）═══════════════
-- 面向 code：positioning 商品定位 / itinerary 行程流程 / fee 費用資訊 / meetup 集合資訊
--           redeem 使用兌換 / group_form 成團條件 / restriction 限制與風險 / sla 承諾與SLA

-- 商品質檢彙總（prod_oid 去重）+ 8 面向。
CREATE TABLE IF NOT EXISTS prod_quality (
    prod_oid          TEXT PRIMARY KEY,
    prod_name         TEXT,
    bd_tag_note       TEXT,
    supplier_oid      TEXT,
    inquiry_count     INTEGER DEFAULT 0,
    order_count       INTEGER DEFAULT 0,
    order_profit_sum  REAL    DEFAULT 0,
    judgments_total   INTEGER DEFAULT 0,
    content_issue_n   INTEGER DEFAULT 0,
    content_issue_pct REAL    DEFAULT 0,
    contract_breach_n INTEGER DEFAULT 0,
    top_dimension     TEXT,
    max_confidence    REAL    DEFAULT 0,
    overall_status    TEXT,
    positioning_n INTEGER DEFAULT 0,
    itinerary_n   INTEGER DEFAULT 0,
    fee_n         INTEGER DEFAULT 0,
    meetup_n      INTEGER DEFAULT 0,
    redeem_n      INTEGER DEFAULT 0,
    group_form_n  INTEGER DEFAULT 0,
    restriction_n INTEGER DEFAULT 0,
    sla_n         INTEGER DEFAULT 0,
    last_judged_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_prod_quality_issue ON prod_quality(content_issue_n DESC);

-- 方案質檢彙總（pkg_oid 去重）+ 8 面向。資料源＝judgments.pkg_oid。
CREATE TABLE IF NOT EXISTS pkg_quality (
    pkg_oid           TEXT PRIMARY KEY,
    prod_oid          TEXT,
    prod_name         TEXT,
    inquiry_count     INTEGER DEFAULT 0,
    judgments_total   INTEGER DEFAULT 0,
    content_issue_n   INTEGER DEFAULT 0,
    content_issue_pct REAL    DEFAULT 0,
    contract_breach_n INTEGER DEFAULT 0,
    top_dimension     TEXT,
    max_confidence    REAL    DEFAULT 0,
    overall_status    TEXT,
    positioning_n INTEGER DEFAULT 0,
    itinerary_n   INTEGER DEFAULT 0,
    fee_n         INTEGER DEFAULT 0,
    meetup_n      INTEGER DEFAULT 0,
    redeem_n      INTEGER DEFAULT 0,
    group_form_n  INTEGER DEFAULT 0,
    restriction_n INTEGER DEFAULT 0,
    sla_n         INTEGER DEFAULT 0,
    last_judged_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_pkg_quality_prod ON pkg_quality(prod_oid);
