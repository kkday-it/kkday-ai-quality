-- ════════════════════════════════════════════════════════════════════
-- AI 法官進線抽取（merged 單一查詢·法典聚焦版）
-- 實測：2026-06-23 於 DAP BigQuery 跑通 → 1651 列 / 44.9 GB（過 50GB 上限）
--
-- 用途：一支 SELECT 同時撈「這兩天進線對話 + 關聯商品/方案 Phase1 R1-R5 检验欄位」，
--       直接匯出（CSV / JSONL）→ 導入本地 kkdb_ai_quality.db。
-- 對照：bigquery_extract.sql 為「2 query 分開」設計（grain 不同、更省成本，生產正解）；
--       本檔為「一次撈完」便利版，成本較高（product/package description_module 全掃 ~36GB）。
--
-- ── 成本約束（實測，見 memory: bigquery-dap-cost-constraints）──
--   • product_summary 23.8GB + package_summary 12.8GB 全掃（無法剪枝）+ message 15.6GB/日
--   • DAP 只准純 SELECT：DECLARE / EXECUTE IMMEDIATE / CREATE TABLE 全擋；
--     子查詢/JOIN/EXISTS 帶 prod_oid 都不觸發 cluster pruning，只有「字面 IN (20,1641,…)」會剪枝。
--   • 降成本：① 縮時間窗（message ∝ 天數）② 2 步字面（step1 算 prod_oid → step2 字面 IN）
--     ③ 請 DAP 開 scripting/物化權限（一次全掃、進線天天 join）。
--
-- ── 欄位來源（對齊內容治理法典 8 面向，Phase1 检验欄位）──
--   商品層 PMDL：PRODUCT_INFO(name/feature/desc) / INTRODUCE_SUMMARY / SCHEDULE(R1) /
--                INC_NINC(R4-1) / VENUE_LOCATION(R2) / EXCHANGE(R2-10) / NOTICE(R3·R4 成團/限制)
--   方案層 PMDL：PACKAGE_INFO / PACKAGE_DESC / SCHEDULE / INC_NINC / VENUE_LOCATION /
--                REFUND_POLICY(退款SLA) + order_process_setting(R3 成團 config 欄位)
--   進線：chatbot(chatbot_messages) + order_message(message_session×message)；主語系 master_lang。
--
-- ⚠️ PII：輸出含 order_mid / aggregated_messages(對話) / zendesk_ticket_id 等真實客戶資料，
--    只放 backend/fixtures/（gitignored），禁止 commit / 上傳外部。
-- ════════════════════════════════════════════════════════════════════
WITH
relevant_prods AS (
    SELECT DISTINCT ms.prod_oid AS prod_oid
    FROM `kkday-data-dap.dw_kkdb.message_session` ms
    WHERE DATE(ms.create_date) BETWEEN '2026-06-22' AND '2026-06-23' AND ms.prod_oid IS NOT NULL
    UNION DISTINCT
    SELECT prod_oid
    FROM `kkday-data-dap.dw_kkdb_chatbot.chatbot_session_lst`
    WHERE DATE(create_date) BETWEEN '2026-06-22' AND '2026-06-23' AND prod_oid IS NOT NULL
),
chatbot_agg AS (
    SELECT session_id,
        STRING_AGG(CONCAT(message_sender, ': ', COALESCE(message_content,'')), '\n'
                   ORDER BY create_date ASC) AS chatbot_conversation
    FROM `kkday-data-dap.dw_kkdb_chatbot.chatbot_messages`
    WHERE DATE(create_date) BETWEEN '2026-06-22' AND '2026-06-23'
    GROUP BY session_id HAVING COUNTIF(message_sender = 'user') >= 1
),
order_msg_agg AS (
    SELECT ms.session_oid, ms.session_direction, ms.supplier_oid,
        ANY_VALUE(ms.prod_oid) AS prod_oid, ANY_VALUE(ms.pkg_oid) AS pkg_oid,
        STRING_AGG(CONCAT(
            CASE m.send_type WHEN 'K' THEN 'KKday 客服' WHEN 'M' THEN 'user'
                             WHEN 'S' THEN '供應商' ELSE m.send_type END,
            ': ', COALESCE(m.msg_content,'')), '\n' ORDER BY m.create_date ASC) AS order_conversation
    FROM `kkday-data-dap.dw_kkdb.message_session` AS ms
    INNER JOIN `kkday-data-dap.dw_kkdb.message` AS m
        ON m.order_oid = ms.order_oid AND m.msg_oid BETWEEN ms.start_msg_oid AND ms.end_msg_oid
    WHERE DATE(ms.create_date) BETWEEN '2026-06-22' AND '2026-06-23'
      AND DATE(m.create_date)  BETWEEN '2026-06-22' AND '2026-06-23'
    GROUP BY ms.session_oid, ms.session_direction, ms.supplier_oid
),
chatbot_sess AS (
    SELECT session_id, prod_oid
    FROM `kkday-data-dap.dw_kkdb_chatbot.chatbot_session_lst`
    WHERE DATE(create_date) BETWEEN '2026-06-22' AND '2026-06-23'
    QUALIFY ROW_NUMBER() OVER(PARTITION BY session_id ORDER BY chatbot_session_lst_oid DESC) = 1
),
-- 商品層（Phase1 检验欄位）
product_content AS (
    SELECT prod_oid, prod_mid, master_lang,
        JSON_VALUE(pj,'$.PMDL_PRODUCT_INFO.name')         AS prod_name,      -- R5-2/5-3 名稱錯位
        JSON_VALUE(pj,'$.PMDL_PRODUCT_INFO.feature')      AS prod_feature,   -- R5 錯位
        JSON_VALUE(pj,'$.PMDL_PRODUCT_INFO.desc')         AS prod_desc,      -- R5 錯位
        JSON_VALUE(pj,'$.PMDL_INTRODUCE_SUMMARY.summary') AS prod_summary,   -- 商品說明
        JSON_QUERY(pj,'$.PMDL_SCHEDULE')                  AS prod_schedule,  -- R1 行程
        JSON_QUERY(pj,'$.PMDL_INC_NINC')                  AS prod_fee,       -- R4-1 保險/費用
        JSON_QUERY(pj,'$.PMDL_VENUE_LOCATION')            AS prod_meetup,    -- R2 集合
        JSON_QUERY(pj,'$.PMDL_EXCHANGE')                  AS prod_exchange,  -- R2-10 兌換
        JSON_QUERY(pj,'$.PMDL_NOTICE')                    AS prod_notice     -- R3/R4 成團/限制/未成團
    FROM (
        SELECT t1.prod_oid, t1.prod_mid, t1.master_lang,
            CASE t1.master_lang
                WHEN 'en'    THEN JSON_QUERY(t1.description_module,'$.en')
                WHEN 'ja'    THEN JSON_QUERY(t1.description_module,'$.ja')
                WHEN 'zh-tw' THEN JSON_QUERY(t1.description_module,'$."zh-tw"')
                WHEN 'ko'    THEN JSON_QUERY(t1.description_module,'$.ko')
                WHEN 'th'    THEN JSON_QUERY(t1.description_module,'$.th')
                WHEN 'vi'    THEN JSON_QUERY(t1.description_module,'$.vi')
                WHEN 'zh-cn' THEN JSON_QUERY(t1.description_module,'$."zh-cn"')
                WHEN 'zh-hk' THEN JSON_QUERY(t1.description_module,'$."zh-hk"')
            END AS pj
        FROM `kkday-data-dap.dw_kkdb_product.product_summary` t1
        WHERE t1.prod_oid IN (SELECT prod_oid FROM relevant_prods)
        QUALIFY ROW_NUMBER() OVER(PARTITION BY t1.prod_oid ORDER BY t1.prod_oid) = 1
    )
),
-- 方案層（Phase1 检验欄位 + 成團 config）
package_all AS (
    SELECT prod_oid,
        TO_JSON_STRING(ARRAY_AGG(STRUCT(
            pkg_oid, pkg_name, pkg_desc, pkg_schedule, pkg_fee, pkg_meetup, pkg_refund, pkg_order_process
        ))) AS packages_json
    FROM (
        SELECT prod_oid, pkg_oid,
            JSON_VALUE(kj,'$.PMDL_PACKAGE_INFO.name') AS pkg_name,        -- R5 名稱
            JSON_QUERY(kj,'$.PMDL_PACKAGE_DESC')      AS pkg_desc,        -- R5 方案描述
            JSON_QUERY(kj,'$.PMDL_SCHEDULE')          AS pkg_schedule,    -- R1 行程
            JSON_QUERY(kj,'$.PMDL_INC_NINC')          AS pkg_fee,         -- R4-1 費用
            JSON_QUERY(kj,'$.PMDL_VENUE_LOCATION')    AS pkg_meetup,      -- R2 集合
            JSON_QUERY(kj,'$.PMDL_REFUND_POLICY')     AS pkg_refund,      -- 退款 SLA
            order_process_setting                      AS pkg_order_process -- R3 成團(確認/發送時效)
        FROM (
            SELECT t2.prod_oid, t2.pkg_oid, t2.order_process_setting,
                CASE t1.master_lang
                    WHEN 'en'    THEN JSON_QUERY(t2.description_module,'$.en')
                    WHEN 'ja'    THEN JSON_QUERY(t2.description_module,'$.ja')
                    WHEN 'zh-tw' THEN JSON_QUERY(t2.description_module,'$."zh-tw"')
                    WHEN 'ko'    THEN JSON_QUERY(t2.description_module,'$.ko')
                    WHEN 'th'    THEN JSON_QUERY(t2.description_module,'$.th')
                    WHEN 'vi'    THEN JSON_QUERY(t2.description_module,'$.vi')
                    WHEN 'zh-cn' THEN JSON_QUERY(t2.description_module,'$."zh-cn"')
                    WHEN 'zh-hk' THEN JSON_QUERY(t2.description_module,'$."zh-hk"')
                END AS kj
            FROM `kkday-data-dap.dw_kkdb_product.package_summary` t2
            JOIN `kkday-data-dap.dw_kkdb_product.product_summary` t1 ON t1.prod_oid = t2.prod_oid  -- 取 master_lang
            WHERE t2.prod_oid IN (SELECT prod_oid FROM relevant_prods)
        )
    )
    GROUP BY prod_oid
)
SELECT
    s.session_oid, s.zendesk_ticket_id, s.create_date AS session_create_date,
    c.order_oid, c.order_mid, sm.sessionable_type, sm.sessionable_id,
    COALESCE(oma.prod_oid, cs.prod_oid) AS prod_oid,
    oma.pkg_oid AS pkg_oid,
    pc.master_lang,
    CASE WHEN sm.sessionable_type = 'order_message' THEN oma.session_direction END AS session_direction,
    CASE WHEN sm.sessionable_type = 'order_message' THEN oma.supplier_oid END AS supplier_oid,
    CASE WHEN sm.sessionable_type = 'chatbot' THEN 'KKDAY' ELSE 'SUPPLIER' END AS msg_handler,
    CASE WHEN sm.sessionable_type = 'chatbot' THEN cb.chatbot_conversation
         WHEN sm.sessionable_type = 'order_message' THEN oma.order_conversation END AS aggregated_messages,
    -- 商品層 Phase1
    pc.prod_name, pc.prod_feature, pc.prod_desc, pc.prod_summary,
    pc.prod_schedule, pc.prod_fee, pc.prod_meetup, pc.prod_exchange, pc.prod_notice,
    -- 方案層 Phase1
    pa.packages_json
FROM `kkday-data-dap.dw_kkdb_imassage.session` AS s
INNER JOIN `kkday-data-dap.dw_kkdb_imassage.channel` AS c ON s.channel_oid = c.channel_oid
INNER JOIN `kkday-data-dap.dw_kkdb_imassage.session_mapping` AS sm ON s.session_oid = sm.session_oid
LEFT JOIN chatbot_agg cb ON sm.sessionable_id = cb.session_id
LEFT JOIN order_msg_agg oma ON SAFE_CAST(sm.sessionable_id AS INT64) = oma.session_oid
LEFT JOIN chatbot_sess cs ON sm.sessionable_id = cs.session_id
LEFT JOIN product_content pc ON COALESCE(oma.prod_oid, cs.prod_oid) = pc.prod_oid
LEFT JOIN package_all   pa ON COALESCE(oma.prod_oid, cs.prod_oid) = pa.prod_oid
WHERE DATE(s.create_date) BETWEEN '2026-06-22' AND '2026-06-23'
  AND ((sm.sessionable_type = 'chatbot' AND cb.chatbot_conversation IS NOT NULL)
    OR (sm.sessionable_type = 'order_message' AND oma.order_conversation IS NOT NULL))
ORDER BY s.create_date DESC;
