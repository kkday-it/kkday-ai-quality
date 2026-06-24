// verdict（判決）相關常數：label / color / key 順序 / 「內容問題」集合。
import { ACTIONABLE_VERDICTS } from '@aipq/types';

/** verdict → 中文 label（卡片標籤、篩選下拉）。插入順序＝heatmap 橫軸順序。 */
export const VERDICT_LABEL: Record<string, string> = {
  real_config_issue: '設定錯誤',
  content_missing: '缺漏',
  content_unclear: '模糊',
  contract_breach: '履約違規',
  customer_misread: '客戶誤解',
  escalate_ops: '非內容',
};

/** verdict → Arco tag 顏色。 */
export const VERDICT_COLOR: Record<string, string> = {
  real_config_issue: 'magenta',
  content_missing: 'red',
  content_unclear: 'orange',
  contract_breach: 'pinkpurple',
  customer_misread: 'gray',
  escalate_ops: 'blue',
};

/** verdict key 順序（heatmap 橫軸 / 篩選 value）。 */
export const VERDICT_KEYS = Object.keys(VERDICT_LABEL);

/** verdict label 順序（與 VERDICT_KEYS 平行，heatmap 橫軸文字）。 */
export const VERDICT_LABELS = VERDICT_KEYS.map((k) => VERDICT_LABEL[k]);

/** 「內容問題」verdict 集合（KPI / 缺口計算），復用 @aipq/types 的 actionable 清單。 */
export const CONTENT_VERDICTS = new Set<string>(ACTIONABLE_VERDICTS);
