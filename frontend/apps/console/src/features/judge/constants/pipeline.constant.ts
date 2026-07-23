// 初判顯示 label——SSOT 為 repo 根 config/ai_judge/prejudge.json（前後端同讀，DB 仍存 code）。
// 收斂原本散落於 AttributionList / useAttributionDashboard 各寫一份的 tier / polarity label 對照。
import prejudgeCfg from '@config/ai_judge/prejudge.json';

/** 信心分層 code → 繁中四字 label（auto_accept / jury / needs_review）。未知 code 由呼叫端回退原值。 */
export const TIER_LABELS: Record<string, string> = prejudgeCfg.tier_labels;

/** 傾向 code → 繁中 label（positive / negative / neutral）。 */
export const POLARITY_LABELS: Record<string, string> = prejudgeCfg.polarity_labels;

/** 初判階段 code → 繁中 label（unjudged / judged / pending_review / pending_data）。 */
export const STAGE_LABELS: Record<string, string> = prejudgeCfg.stage_labels;

/** 傾向類別 Arco tag 色（正向綠 / 負向紅 / 中性灰）——列表 / 詳情抽屜 / 執行日誌三處共用（純前端 UI 常數）。 */
export const POLARITY_COLOR: Record<string, string> = {
  positive: 'green',
  negative: 'red',
  neutral: 'gray',
};

/** 初判階段語義色（未初判灰 / 已初判綠 / 待複審橙 / 待數據補充藍）。 */
export const STAGE_COLOR: Record<string, string> = {
  unjudged: 'gray',
  judged: 'green',
  pending_review: 'orange',
  pending_data: 'arcoblue',
};

/**
 * 信心數字按分層上色（config confidence_tiers 驅動的 tier）：
 * auto_accept(≥0.8) 綠＝可採信 / jury(0.5–0.8) 琥珀＝需複審 / needs_review(<0.5) 紅＝必人工。
 * 讓判決者掃一眼信心色就知哪條要處理（呼應「< 0.8 需人工判決」）。
 */
export const CONF_TIER_CLASS: Record<string, string> = {
  auto_accept: 'text-[rgb(var(--success-6))]',
  jury: 'text-[rgb(var(--warning-6))]',
  needs_review: 'text-[rgb(var(--danger-6))]',
};
