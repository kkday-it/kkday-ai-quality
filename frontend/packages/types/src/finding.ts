// AI 法官前端型別（對應後端 backend/app/schema.py 的 Pydantic 模型）
// 前後端雙語言，靠這份 + 後端 schema 保持 REST/JSON contract 對齊。

export const DIMENSIONS = [
  '商品定位',
  '行程流程',
  '費用資訊',
  '集合資訊',
  '使用兌換',
  '成團條件',
  '限制與風險',
  '承諾與SLA',
] as const;
export type Dimension = (typeof DIMENSIONS)[number] | 'non_content';

export type LogicalField =
  | 'prod_name'
  | 'prod_summary'
  | 'prod_feature'
  | 'prod_schedules'
  | 'pkg_desc'
  | 'pkg_schedules'
  | 'none';

export type RecommendedAction =
  | 'rewrite_field'
  | 'fix_contradiction'
  | 'add_missing_info'
  | 'clarify_wording'
  | 'no_action'
  | 'escalate_ops'
  | 'escalate_ux';

export interface AdequacyResult {
  status: 'adequate' | 'unclear' | 'missing' | 'contradictory' | 'field_empty';
  evidence: string;
  reason: string;
}

export interface TicketFinding {
  finding_id: string;
  ticket_id: string;
  prod_oid: string;
  pkg_oid: string;
  order_oid: string; // 訂單編號（B 客人進線可定位；對齊後端 schema.py）
  supplier_oid: string; // 供應商編號（order_message 進線可定位；對齊後端 schema.py）
  dimension: Dimension;
  problem_summary: string;
  suspected_field: LogicalField;
  evidence_quote: string;
  ground_truth_quote: string;
  confidence: number;
  adequacy_check?: AdequacyResult;
  recommended_action: RecommendedAction;
  action_detail: string;
  writer_handoff: boolean;
  is_primary: boolean;
  status: 'new' | 'confirmed' | 'dismissed' | 'fixed' | 'data_missing';
  created_at: string;
}
