-- ════════════════════════════════════════════════════════════════════
-- BigQuery 抽取 SQL（kkday-data-dap）→ 匯出 CSV → 導入本地 kkdb_ai_quality.db
--
-- 兩段「分開跑、分開匯出」（grain 不同，禁合併成一個 JOIN）：
--   Query A 進線     → inquiries / orders / suppliers / products(name,bd_tag)
--                       本地：.venv/bin/python -m app.core.roster <A.csv>
--   Query B 商品內容 → products(內容欄) / packages
--                       本地：load_product_content(<B.csv>)  或 -m app.core.roster <A.csv> <B.csv>
--
-- ⚠️ 為何不合併：A 的 grain=進線 session（一列一客訴），B 的 grain=商品×方案（受審原文）。
--    硬 JOIN 會讓每筆進線拖整包商品長文、且沒進線的商品消失 → 違反本地正規化 10 表。
--    正解：各自匯出，本地靠 prod_oid 關聯。
--
-- 8 面向定位資料：B 已填 6/8 面向 PMDL 路徑（✅已證實 / ⚠️module確定·summary leaf待跑一次驗，
--    錯只回 NULL 不報錯）。成團條件 / 承諾與SLA 不在 description_module（系統 config，另接來源）。
--    路徑出處：過往 Confluence 商品模組對照頁（見 memory pmdl-dimension-mapping）。
-- ════════════════════════════════════════════════════════════════════


-- ╔══════════════════════════════════════════════════════════════════╗
-- ║ Query A — 售後進線（→ inquiries / orders / suppliers / products）  ║
-- ╚══════════════════════════════════════════════════════════════════╝
-- 輸出欄位已對齊本地 load_intake；無語系欄位（對話為客人原語言），zh-tw 過濾不適用此段。
WITH chatbot_agg AS (
    SELECT
        session_id,
        STRING_AGG(CONCAT(message_sender, ': ', COALESCE(message_content, '')), '\n'
                   ORDER BY create_date ASC) AS chatbot_conversation
    FROM `kkday-data-dap.dw_kkdb_chatbot.chatbot_messages`
    GROUP BY session_id
    HAVING COUNTIF(message_sender = 'user') >= 1
),
order_msg_agg AS (
    SELECT
        ms.session_oid, ms.session_direction, ms.supplier_oid,
        STRING_AGG(CONCAT(
            CASE m.send_type WHEN 'K' THEN 'KKday 客服' WHEN 'M' THEN 'user'
                             WHEN 'S' THEN '供應商' ELSE m.send_type END,
            ': ', COALESCE(m.msg_content, '')), '\n' ORDER BY m.create_date ASC) AS order_conversation
    FROM `kkday-data-dap.dw_kkdb.message_session` AS ms
    INNER JOIN `kkday-data-dap.dw_kkdb.message` AS m
        ON m.order_oid = ms.order_oid AND m.msg_oid BETWEEN ms.start_msg_oid AND ms.end_msg_oid
    GROUP BY ms.session_oid, ms.session_direction, ms.supplier_oid
)
SELECT
    s.session_oid, s.zendesk_ticket_id, s.create_date AS session_create_date,
    c.order_oid, c.order_mid, sm.sessionable_type, sm.sessionable_id, ol.prod_oid,
    CASE WHEN sm.sessionable_type = 'order_message' THEN oma.session_direction END AS session_direction,
    CASE WHEN sm.sessionable_type = 'order_message' THEN oma.supplier_oid END AS supplier_oid,
    CASE WHEN sm.sessionable_type = 'chatbot' THEN 'KKDAY' ELSE sup.msg_handler END AS msg_handler,
    CASE WHEN sm.sessionable_type = 'chatbot' THEN cb.chatbot_conversation
         WHEN sm.sessionable_type = 'order_message' THEN oma.order_conversation END AS aggregated_messages,
    te.prod_bd_tag_note, te.prod_name_zh_tw, te.order_profit
FROM `kkday-data-dap.dw_kkdb_imassage.session` AS s
INNER JOIN `kkday-data-dap.dw_kkdb_imassage.channel` AS c ON s.channel_oid = c.channel_oid
INNER JOIN `kkday-data-dap.dw_kkdb_imassage.session_mapping` AS sm ON s.session_oid = sm.session_oid
LEFT JOIN (
    SELECT order_oid, prod_oid FROM `kkday-data-dap.dw_kkdb.order_lst`
    QUALIFY ROW_NUMBER() OVER(PARTITION BY order_oid ORDER BY prod_oid DESC) = 1
) AS ol ON c.order_oid = ol.order_oid
LEFT JOIN (
    SELECT order_oid, prod_bd_tag_note, prod_name_zh_tw, order_profit
    FROM `kkday-data-dap.dm_tableau.tableau_ec_index`
    WHERE order_create_source_code != 'B2D'
    QUALIFY ROW_NUMBER() OVER(PARTITION BY order_oid ORDER BY order_profit DESC) = 1
) AS te ON c.order_oid = te.order_oid
LEFT JOIN chatbot_agg cb ON sm.sessionable_id = cb.session_id
LEFT JOIN order_msg_agg oma ON SAFE_CAST(sm.sessionable_id AS INT64) = oma.session_oid
LEFT JOIN `kkday-data-dap.dw_kkdb.supplier` AS sup ON oma.supplier_oid = sup.supplier_oid
WHERE DATE(s.create_date) BETWEEN '2026-04-01' AND '2026-04-02'   -- 依需求調整區間
  AND ((sm.sessionable_type = 'chatbot' AND cb.chatbot_conversation IS NOT NULL)
    OR (sm.sessionable_type = 'order_message' AND oma.order_conversation IS NOT NULL))
ORDER BY s.create_date DESC;


-- ╔══════════════════════════════════════════════════════════════════╗
-- ║ Query B — 商品/方案內容（→ products 內容欄 / packages）           ║
-- ║ zh-tw only（其他語系已註釋）；輸出欄位已對齊本地 load_product_content ║
-- ╚══════════════════════════════════════════════════════════════════╝
WITH base_data AS (
    SELECT
        t1.prod_oid, t1.prod_mid, t2.pkg_oid, t1.master_lang,
        JSON_EXTRACT(t1.description_module, '$.') AS prod_json,
        JSON_EXTRACT(t2.description_module, '$.') AS pkg_json
    FROM `kkday-data-dap.dw_kkdb_product.product_summary` t1
    JOIN `kkday-data-dap.dw_kkdb_product.package_summary` t2 ON t1.prod_oid = t2.prod_oid
    JOIN `kkday-data-dap.dw_kkdb_product.product_config`  t6 ON t1.prod_oid = t6.prod_oid
    INNER JOIN `kkday-data-dap.team_pm.tour_product_list` t5 ON t1.prod_oid = t5.prod_oid
    JOIN `kkday-data-dap.dm_tableau.tableau_bd_prod_item` t3 ON t2.pkg_oid = t3.pkg_oid
    WHERE t6.is_active = TRUE
      AND t3.pkg_sale_stat_indic = 'Y'
      AND REGEXP_CONTAINS(t6.allow_sale_channel, r'web|ios|android')
),
lang_extracted AS (
    SELECT
        prod_oid, prod_mid, pkg_oid, master_lang,
        -- 【暫時只取 zh-tw；其他語系先註釋，要時解開】
        JSON_QUERY(prod_json, '$."zh-tw"') AS prod_main,
        -- JSON_QUERY(prod_json, '$.en')      -- en
        -- JSON_QUERY(prod_json, '$.ja')      -- ja
        -- JSON_QUERY(prod_json, '$.ko')      -- ko
        -- JSON_QUERY(prod_json, '$.th')      -- th
        -- JSON_QUERY(prod_json, '$.vi')      -- vi
        -- JSON_QUERY(prod_json, '$."zh-cn"') -- zh-cn
        -- JSON_QUERY(prod_json, '$."zh-hk"') -- zh-hk
        JSON_QUERY(pkg_json, '$."zh-tw"') AS pkg_main
    FROM base_data
    -- WHERE master_lang = 'zh-tw'   -- 若只要母語為 zh-tw 的商品，解開此行
)
SELECT
    prod_oid,
    prod_mid,
    master_lang,
    pkg_oid,
    -- ✅ 已證實路徑（你的 SQL / Confluence summary 形）
    JSON_VALUE(prod_main, '$.PMDL_INTRODUCE_SUMMARY.summary')        AS prod_summary,    -- 商品定位·商品說明
    JSON_QUERY(prod_main, '$.PMDL_SCHEDULE.tourinfo[0].schedules')   AS prod_schedules,  -- 行程流程·商品層
    JSON_QUERY(prod_main, '$.PMDL_NOTICE.cust_reminds')             AS prod_notice,     -- 限制與風險·注意事項
    JSON_VALUE(pkg_main,  '$.PMDL_PACKAGE_DESC.notes[0].value')      AS pkg_desc,        -- 方案描述
    JSON_QUERY(pkg_main,  '$.PMDL_SCHEDULE.tourinfo[0].schedules')   AS pkg_schedules,   -- 行程流程·方案層
    JSON_VALUE(pkg_main,  '$.PMDL_PACKAGE_INFO.name')               AS pkg_name,        -- 方案名稱

    -- ⚠️ 來自 Confluence 模組對照（module 確定、summary leaf 待跑一次驗；錯只回 NULL 不報錯）
    JSON_VALUE(prod_main, '$.PMDL_PRODUCT_INFO.name')              AS prod_name,       -- 商品定位·商品名稱
    JSON_VALUE(prod_main, '$.PMDL_PRODUCT_INFO.feature')           AS prod_feature,    -- 商品定位·商品特色
    JSON_VALUE(prod_main, '$.PMDL_PRODUCT_INFO.desc')             AS prod_desc,       -- 商品定位·商品簡述
    JSON_QUERY(prod_main, '$.PMDL_INC_NINC')                      AS prod_fee,        -- 費用資訊·包含/不包含
    JSON_QUERY(prod_main, '$.PMDL_VENUE_LOCATION')                AS prod_meetup,     -- 集合資訊·集合地點(+PMDL_TOUR.meetup_time)
    JSON_QUERY(prod_main, '$.PMDL_EXCHANGE_LOCATION')             AS prod_redeem,     -- 使用兌換·兌換地點(+PMDL_EXCHANGE_VALID)
    JSON_VALUE(prod_main, '$.PMDL_PURCHASE_SUMMARY.summary')       AS prod_purchase    -- 使用兌換·購買須知

    -- ❌ 成團條件 / 承諾與SLA：不在 description_module（商品/訂單系統 config 欄位，需另接來源表）
FROM lang_extracted
ORDER BY prod_oid, pkg_oid;
