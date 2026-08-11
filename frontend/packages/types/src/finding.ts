// AI 法官前端型別（對應後端 backend/app/core/schema.py 的 Pydantic 模型）
// 前後端雙語言，靠這份 + 後端 schema 保持 REST/JSON contract 對齊。

/** 建議動作（鍵齊 backend schema.RecommendedAction 的 8 個 Literal）。 */
export type RecommendedAction =
  | 'rewrite_field'
  | 'fix_contradiction'
  | 'add_missing_info'
  | 'clarify_wording'
  | 'penalize_breach'
  | 'no_action'
  | 'escalate_ops'
  | 'escalate_ux';

/** 證據層級（漸進升級：純症狀 → 有商品頁 → 有訂單 → 兩者皆有）；初判硬閘依此封鎖履約不符歸因。 */

/** 嚴重度（軸B · ITIL Priority）。 */

/** 初判單元（SSOT）。 */
export interface TicketFinding {
  /** 特徵 id（source_id）；落庫時進 attribution_tbl.source_id。 */
  ticket_id: string;
  pkg_oid: string;
  /** 反饋摘要：語系 → 簡明摘要 map（務必含 'zh-tw'；表格只顯示 zh-tw）。 */
  summary: Record<string, string>;
  /** 逐字原文佐證（防捏造的 grounding 錨點，非摘要）。 */
  evidence_quote: string;
  /** 最終信心（raw → 灰度複判 → cap 封頂 → 線上校準後值）。 */
  confidence: number;
  /** arbiter LLM 原始信心（校準輸入）。 */
  raw_confidence: number;
  recommended_action: RecommendedAction;
  is_primary: boolean;
  /** 高信心且已判定 → 系統自動採納，不進人工佇列。 */
  is_auto_accepted: boolean;
  /** ISO 8601。 */
  created_at: string;
  /** 訂單編號（B 客人進線可定位具體訂單；A/C 管道通常為空）。 */
  order_oid: string;
  /** 供應商編號（order_message 進線可定位）。 */
  supplier_oid: string;
  /** L1 域機器碼（content/supplier…）。 */
  l1_domain_code: string;
  l1_label: string;
  /** L2 面向 C-code（C-x-y）。 */
  l2_code: string;
  l2_label: string;
  /** 正負傾向：positive / negative / neutral。 */
  polarity: string;
  /** 情緒分 1-5（夾進 polarity 區間）；0＝未初判。 */
  sentiment_score: number;
  /** 信心分層：auto_accept / jury / needs_review。 */
  confidence_tier: string;
}
