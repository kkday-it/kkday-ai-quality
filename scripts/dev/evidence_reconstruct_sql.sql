-- 訂單佐證「拆欄版」重建 SQL——輸出的每一欄對應 evidence_snapshot 的一個真實欄位
-- （非單一 payload jsonb），可逐欄與 evidence_snapshot 存的列直接核對。
-- 對 production snapshot（kkdb）執行；把 :oid 換成 order_oid（psql 用 \set oid 49446327，或字面替換）。
WITH ot AS (
  SELECT order_mid, order_status, price_pay, lang_code, crt_dt
  FROM order_tbl WHERE order_oid = :oid
),
ol AS (
  SELECT prod_oid, prod_version, prod_level2_oid AS pkg_oid, item_oid, supplier_oid,
         lst_dt_go, timezone, prod_level2_name AS pkg_name, prod_desc
  FROM order_lst WHERE order_oid = :oid
  ORDER BY order_lst_oid LIMIT 1
),
p AS (  -- 解析商品/供應商點查參數：pkg 維度鍵=order_lst.prod_level2_oid、語系=order_tbl.lang_code（缺則 zh-tw）
  SELECT ol.prod_oid, ol.prod_version AS ver, ol.pkg_oid, ol.supplier_oid,
         COALESCE(NULLIF(TRIM(ot.lang_code), ''), 'zh-tw') AS lang
  FROM ol CROSS JOIN ot
),
sup AS (
  SELECT supplier_name, order_handler AS supplier_order_handler, msg_handler AS supplier_msg_handler
  FROM supplier, p WHERE supplier.supplier_oid = p.supplier_oid
),
pl AS (  -- ors_prod_lang → item_lang / package_lang 兩欄
  SELECT ors_prod_lang.prod_lang->'item_summary'    AS item_lang,
         ors_prod_lang.prod_lang->'package_summary' AS package_lang
  FROM ors_prod_lang, p
  WHERE ors_prod_lang.prod_oid = p.prod_oid AND ors_prod_lang.prod_version = p.ver
    AND ors_prod_lang.lang_code = p.lang
),
ps AS (  -- ors_prod_setting → product_summary / product_desc_module / item_setting / package_setting 四欄
  SELECT
    jsonb_build_object(
      'timezone',         prod_setting->'product_summary'->'timezone',
      'category',         prod_setting->'product_summary'->'category',
      'product_name',     prod_setting->'product_summary'->'product_name'->p.lang,
      'sale_time_result', prod_setting->'product_summary'->'sale_time_result'
    ) AS product_summary,
    prod_setting->'product_summary'->'description_module'->p.lang AS product_desc_module,
    prod_setting->'item_summary' AS item_setting,
    (
      SELECT jsonb_agg(pkg) FROM jsonb_array_elements(prod_setting->'package_summary') pkg
      WHERE (pkg->>'pkg_oid')::bigint = p.pkg_oid
    ) AS package_setting
  FROM ors_prod_setting, p
  WHERE ors_prod_setting.prod_oid = p.prod_oid AND ors_prod_setting.prod_version = p.ver
),
pb AS (
  SELECT jsonb_build_object('cancel_policy_client', cancel_policy_client, 'tour_duration', tour_duration)
    AS package_policy
  FROM ors_pkg_basic, p
  WHERE ors_pkg_basic.prod_oid = p.prod_oid AND ors_pkg_basic.prod_version = p.ver
    AND ors_pkg_basic.pkg_oid = p.pkg_oid
),
ms AS (
  SELECT jsonb_agg(jsonb_build_object('prod_module_type', prod_module_type, 'prod_module_setting', prod_module_setting))
    AS package_module_setting
  FROM ors_prod_module_setting, p
  WHERE ors_prod_module_setting.prod_oid = p.prod_oid AND ors_prod_module_setting.prod_version = p.ver
    AND ors_prod_module_setting.pkg_oid = p.pkg_oid AND ors_prod_module_setting.lang_code = p.lang
)
SELECT
  ot.order_mid, ot.order_status, ot.price_pay, ot.lang_code, ot.crt_dt,
  ol.prod_oid, ol.prod_version, ol.pkg_oid, ol.item_oid, ol.supplier_oid,
  ol.lst_dt_go, ol.timezone, ol.pkg_name, ol.prod_desc,
  sup.supplier_name, sup.supplier_order_handler, sup.supplier_msg_handler,
  ps.product_summary, ps.product_desc_module,
  pl.item_lang, ps.item_setting,
  pl.package_lang, ps.package_setting, pb.package_policy, ms.package_module_setting
FROM ot, ol
LEFT JOIN sup ON true
LEFT JOIN pl  ON true
LEFT JOIN ps  ON true
LEFT JOIN pb  ON true
LEFT JOIN ms  ON true;
