-- ════════════════════════════════════════════════════════════════════
-- 方案①：當批 prod_oid「字面剪枝」即時撈最新商品/方案內容（kkday-data-dap）
--
-- 用途：判決前對「本批進線涉及的商品」即時撈最新內容 → upsert 本地 DB → judge 查 DB。
--       同時解決兩個約束：
--         ① 成本：字面 IN 觸發 product_summary / package_summary 的 cluster pruning
--                （兩表皆 cluster by prod_oid）→ 從 ~36GB 全掃剪到 MB–幾GB。
--         ② 新鮮度：每批撈「當下最新」，不靠低頻全量快取，避免「商家已修→誤報 / 新問題→漏判」。
--
-- ⚠️ 占位 __PROD_OIDS__ 由後端 product_refresh.build_product_content_sql 以
--    「字面整數清單」取代，例：WHERE prod_oid IN (150665,88102,203344)。
--    字面 IN 是唯一觸發 cluster pruning 的寫法——子查詢 / JOIN / EXISTS 都不剪枝
--    （實測見 memory bigquery-dap-cost-constraints）。DAP 擋 scripting，故注入在 Python 端。
--
-- 輸出欄位已對齊 roster.upsert_product_content_rows（一列＝一組 prod×pkg；無方案則 pkg 欄 NULL）。
-- 語系由 master_lang 決定（CASE）；路徑錯誤只回 NULL 不報錯。
-- ════════════════════════════════════════════════════════════════════
WITH prod_lang AS (
    SELECT
        t1.prod_oid, t1.prod_mid, t1.master_lang,
        CASE t1.master_lang
            WHEN 'en'    THEN JSON_QUERY(t1.description_module, '$.en')
            WHEN 'ja'    THEN JSON_QUERY(t1.description_module, '$.ja')
            WHEN 'ko'    THEN JSON_QUERY(t1.description_module, '$.ko')
            WHEN 'th'    THEN JSON_QUERY(t1.description_module, '$.th')
            WHEN 'vi'    THEN JSON_QUERY(t1.description_module, '$.vi')
            WHEN 'zh-cn' THEN JSON_QUERY(t1.description_module, '$."zh-cn"')
            WHEN 'zh-hk' THEN JSON_QUERY(t1.description_module, '$."zh-hk"')
            ELSE              JSON_QUERY(t1.description_module, '$."zh-tw"')
        END AS prod_main
    FROM `kkday-data-dap.dw_kkdb_product.product_summary` t1
    WHERE t1.prod_oid IN (__PROD_OIDS__)          -- ← 字面剪枝（product_summary cluster by prod_oid）
    QUALIFY ROW_NUMBER() OVER(PARTITION BY t1.prod_oid ORDER BY t1.prod_oid) = 1
),
pkg_lang AS (
    SELECT
        t2.prod_oid, t2.pkg_oid,
        CASE p.master_lang
            WHEN 'en'    THEN JSON_QUERY(t2.description_module, '$.en')
            WHEN 'ja'    THEN JSON_QUERY(t2.description_module, '$.ja')
            WHEN 'ko'    THEN JSON_QUERY(t2.description_module, '$.ko')
            WHEN 'th'    THEN JSON_QUERY(t2.description_module, '$.th')
            WHEN 'vi'    THEN JSON_QUERY(t2.description_module, '$.vi')
            WHEN 'zh-cn' THEN JSON_QUERY(t2.description_module, '$."zh-cn"')
            WHEN 'zh-hk' THEN JSON_QUERY(t2.description_module, '$."zh-hk"')
            ELSE              JSON_QUERY(t2.description_module, '$."zh-tw"')
        END AS pkg_main
    FROM `kkday-data-dap.dw_kkdb_product.package_summary` t2
    JOIN prod_lang p ON p.prod_oid = t2.prod_oid
    WHERE t2.prod_oid IN (__PROD_OIDS__)          -- ← 字面剪枝（package_summary cluster by prod_oid）
)
SELECT
    p.prod_oid, p.prod_mid, p.master_lang, k.pkg_oid,
    -- 商品層 9 邏輯欄位（對齊整合版父頁 LOGICAL_FIELDS 映射）
    JSON_VALUE(p.prod_main, '$.PMDL_PRODUCT_INFO.name')           AS prod_name,      -- 商品定位·名稱
    JSON_VALUE(p.prod_main, '$.PMDL_INTRODUCE_SUMMARY.summary')   AS prod_summary,   -- 商品定位·說明
    JSON_VALUE(p.prod_main, '$.PMDL_PRODUCT_INFO.feature')        AS prod_feature,   -- 商品定位·特色
    JSON_VALUE(p.prod_main, '$.PMDL_PRODUCT_INFO.desc')           AS prod_desc,      -- 商品定位·簡述
    JSON_QUERY(p.prod_main, '$.PMDL_SCHEDULE')                    AS prod_schedules, -- 行程流程
    JSON_QUERY(p.prod_main, '$.PMDL_NOTICE')                      AS prod_notice,    -- 限制與風險·注意事項
    JSON_QUERY(p.prod_main, '$.PMDL_INC_NINC')                    AS prod_fee,       -- 費用資訊
    JSON_QUERY(p.prod_main, '$.PMDL_VENUE_LOCATION')             AS prod_meetup,    -- 集合資訊
    JSON_QUERY(p.prod_main, '$.PMDL_EXCHANGE_LOCATION')          AS prod_redeem,    -- 使用兌換
    JSON_VALUE(p.prod_main, '$.PMDL_PURCHASE_SUMMARY.summary')    AS prod_purchase,  -- 使用兌換·購買須知
    -- 方案層
    JSON_VALUE(k.pkg_main,  '$.PMDL_PACKAGE_INFO.name')           AS pkg_name,
    JSON_VALUE(k.pkg_main,  '$.PMDL_PACKAGE_DESC.notes[0].value') AS pkg_desc,
    JSON_QUERY(k.pkg_main,  '$.PMDL_SCHEDULE')                    AS pkg_schedules
FROM prod_lang p
LEFT JOIN pkg_lang k ON k.prod_oid = p.prod_oid
ORDER BY p.prod_oid, k.pkg_oid;
