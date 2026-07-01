// AI 法官前端共用工具。

/**
 * 後端 finding row（外層 meta + 巢狀 finding）攤平成單層卡片資料。
 * Analytics 與 ProductDetail 共用，避免兩處各寫一份。
 * @param r 後端回傳的單筆 finding row（含 finding 子物件與外層欄位）
 * @returns 攤平後可直接餵給 FindingCard 的物件
 */
export const flatFinding = (r: any) => ({
  ...r.finding,
  finding_id: r.finding_id,
  prod_oid: r.prod_oid,
  dimension: r.dimension,
  confidence: r.confidence,
  status: r.status,
});
