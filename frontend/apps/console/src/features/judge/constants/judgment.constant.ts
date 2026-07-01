// 判決顯示 label——SSOT 為 repo 根 config/global/judgment.json（前後端同讀，DB 仍存 code）。
// 收斂原本散落於 AttributionList / useAttributionDashboard 各寫一份的 tier / polarity label 對照。
import judgment from '@config/global/judgment.json';

/** 信心分層 code → 繁中四字 label（auto_accept / jury / needs_review / hold）。未知 code 由呼叫端回退原值。 */
export const TIER_LABELS: Record<string, string> = judgment.tier_labels;

/** 傾向 code → 繁中 label（positive / negative / neutral / unknown）。 */
export const POLARITY_LABELS: Record<string, string> = judgment.polarity_labels;
