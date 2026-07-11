// 歸因列表篩選狀態 SSOT：型別 + 空值 + 選項 + 計數 + → API 參數轉換。
// 三處共用（工具列 / 導出彈窗 / 初判目標篩選）皆以此型別為單一真相，避免各寫一份而漂移。
import { STAGE_LABELS, TIER_LABELS } from './judgment.constant';
import { STATUS_LABEL } from './status.constant';

/** 歸因列表可篩選欄位（值型別對齊各控制項 v-model）。 */
export interface AttributionFilters {
  /** 傾向（多選 negative/neutral/positive；分別對應情緒分 1-2 / 3 / 4-5）。預設不選＝不篩選。 */
  polarity: string[];
  /** 判決階段（多選）。 */
  stage: string[];
  /** 信心分層（單選）。 */
  tier: string;
  /** 覆核狀態（多選 new/auto_confirmed/confirmed/dismissed；任一歸因命中即列出）。 */
  status: string[];
  /** 歸因分類（多選任意層級 code；L1/L2/L3 皆可，後端子樹語義命中）。 */
  taxonomy: string[];
  /** 有無外部評論（''=全部 / 'true'=有 / 'false'=無）。 */
  hasExternal: string;
  /** 反饋時間區間 [from, to]（'YYYY-MM-DD'）。 */
  dateRange: string[];
  /** 評論 rec_oid 精確。 */
  recOid: string;
  /** 商品 prod_oid 精確。 */
  prodOid: string;
  /** 訂單 order_oid 精確。 */
  orderOid: string;
}

/** 可渲染的篩選欄位鍵（`<AttributionFilterBar>` 的 fields 取值）。 */
export type FilterField = keyof AttributionFilters;

/** 初始篩選（列表初始 / 重置 / 導出草稿種子）。傾向預設不選（＝不篩選）。 */
export const emptyFilters = (): AttributionFilters => ({
  polarity: [],
  stage: [],
  tier: '',
  status: [],
  taxonomy: [],
  hasExternal: '',
  dateRange: [],
  recOid: '',
  prodOid: '',
  orderOid: '',
});

/** 深拷貝一份篩選（種子彈窗草稿用，避免與來源共用參照）。 */
export const cloneFilters = (f: AttributionFilters): AttributionFilters => ({
  ...f,
  polarity: [...f.polarity],
  stage: [...f.stage],
  status: [...f.status],
  taxonomy: [...f.taxonomy],
  dateRange: [...f.dateRange],
});

/** 有無外部評論選項（''=全部 由 allow-clear 表達）。 */
export const HAS_EXTERNAL_OPTS = [
  { value: 'true', label: '有外部評論' },
  { value: 'false', label: '無外部評論' },
];

/** 傾向篩選選項（順序：負向/中立/正向；直接按 judgments.polarity 篩）。 */
export const POLARITY_FILTER_OPTS = [
  { value: 'negative', label: '負向' },
  { value: 'neutral', label: '中立' },
  { value: 'positive', label: '正向' },
];
/** 階段 / 分層 / 覆核狀態選項（自 label 常數衍生，單一真相）。 */
export const STAGE_OPTS = Object.entries(STAGE_LABELS).map(([value, label]) => ({ value, label }));
export const TIER_OPTS = Object.entries(TIER_LABELS).map(([value, label]) => ({ value, label }));
export const STATUS_OPTS = Object.entries(STATUS_LABEL).map(([value, label]) => ({ value, label }));

/** 已套用的篩選項數（計數徽章用；空值不計）。 */
export const countActiveFilters = (f: AttributionFilters): number =>
  (f.polarity.length ? 1 : 0) +
  (f.stage.length ? 1 : 0) +
  (f.tier ? 1 : 0) +
  (f.status.length ? 1 : 0) +
  (f.taxonomy.length ? 1 : 0) +
  (f.hasExternal ? 1 : 0) +
  (f.dateRange.length ? 1 : 0) +
  (f.recOid.trim() ? 1 : 0) +
  (f.prodOid.trim() ? 1 : 0) +
  (f.orderOid.trim() ? 1 : 0);

/** 篩選 → getProblems / 導出 API 參數（統一轉換，空值一律 undefined 不送）。
 *  傾向直接按 judgments.polarity 多選篩（正向/中性/負向）。 */
export const filtersToParams = (f: AttributionFilters) => {
  return {
    polarity: f.polarity.length ? f.polarity : undefined,
    stage: f.stage.length ? f.stage : undefined,
    confidenceTier: f.tier || undefined,
    status: f.status.length ? f.status : undefined,
    taxonomy: f.taxonomy.length ? f.taxonomy : undefined,
    hasExternal: f.hasExternal || undefined,
    dateFrom: f.dateRange?.[0] || undefined,
    dateTo: f.dateRange?.[1] || undefined,
    recOid: f.recOid.trim() || undefined,
    prodOid: f.prodOid.trim() || undefined,
    orderOid: f.orderOid.trim() || undefined,
  };
};
