/**
 * 人工糾正與待審建議的顯示常數。
 *
 * 值域本身（可改欄白名單、理由門檻）由後端 `/api/attributions/correction-policy` 供給——
 * 那是業務會調的設定，SSOT 在 `config/ai_judge/correction.json`，前端不另抄一份。
 */

/** 糾正抽屜的四種操作模式。 */
export const CORRECTION_MODES = [
  { value: 'correct', label: '修改歸因' },
  { value: 'create', label: '新增遺漏歸因' },
  { value: 'delete', label: '標記 AI 誤判' },
] as const;

export type CorrectionMode = (typeof CORRECTION_MODES)[number]['value'];

/** 待審建議的三種變更型別。 */
export const CHANGE_TYPE_LABELS: Record<string, string> = {
  replace: '修改',
  add: '新增',
  remove: '移除',
};

/** 變更型別的標籤色（Arco color token 名）。 */
export const CHANGE_TYPE_COLORS: Record<string, string> = {
  replace: 'orange',
  add: 'arcoblue',
  remove: 'gray',
};

/** 複審狀態標籤。 */
export const REVIEW_STATUS_LABELS: Record<string, string> = {
  unreviewed: '未複審',
  confirmed: '已確認正確',
  corrected: '已人工糾正',
};

/** 列表「人工介入狀態」篩選選項（對應後端 list_problems 的 human_state）。 */
export const HUMAN_STATE_OPTS = [
  { value: 'ai_only', label: 'AI 原判' },
  { value: 'corrected', label: '已人工介入' },
  { value: 'suggested', label: '有待審建議' },
];

/** 可改欄位的中文名（糾正抽屜與事件時間軸顯示 delta 用）。 */
export const FIELD_LABELS: Record<string, string> = {
  l1_code: '歸因域',
  l2_code: '歸因面向',
  polarity: '情緒傾向',
  sentiment_score: '情緒分',
};
