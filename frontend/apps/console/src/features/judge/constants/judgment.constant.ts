// 判決顯示 label——SSOT 為 repo 根 config/ai_judge/judgment.json（前後端同讀，DB 仍存 code）。
// 收斂原本散落於 AttributionList / useAttributionDashboard 各寫一份的 tier / polarity label 對照。
import judgment from '@config/ai_judge/judgment.json';

/** 信心分層 code → 繁中四字 label（auto_accept / jury / needs_review）。未知 code 由呼叫端回退原值。 */
export const TIER_LABELS: Record<string, string> = judgment.tier_labels;

/** 傾向 code → 繁中 label（positive / negative / neutral）。 */
export const POLARITY_LABELS: Record<string, string> = judgment.polarity_labels;

/** 判決階段 code → 繁中 label（unjudged / judged / pending_review / pending_data）。 */
export const STAGE_LABELS: Record<string, string> = judgment.stage_labels;
