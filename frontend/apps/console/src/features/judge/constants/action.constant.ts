// 建議動作顯示常數。

/** 建議動作 → 中文 label（鍵齊 backend schema.RecommendedAction 8 個 Literal，缺一則該 code 顯示原文）。 */
export const ACTION_LABEL: Record<string, string> = {
  rewrite_field: '重寫欄位',
  fix_contradiction: '修正矛盾',
  add_missing_info: '補充缺漏',
  clarify_wording: '改寫釐清',
  penalize_breach: '計點違規',
  no_action: '無需動作',
  escalate_ops: '轉其他單位',
  escalate_ux: 'UX 議題',
};
