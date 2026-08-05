// 歸因列表篩選狀態 SSOT：型別 + 空值 + 選項 + 計數 + → API 參數轉換。
// 三處共用（工具列 / 導出彈窗 / 初判目標篩選）皆以此型別為單一真相，避免各寫一份而漂移。
// ⚠️ 商品垂直分類篩選**不在**此型別內：它是全局跨來源篩選（bd_tag 維度），狀態在 verticalFilter.store。
import { POLARITY_LABELS, STAGE_LABELS, TIER_LABELS } from './pipeline.constant';
import { BUCKET_LABELS } from './inbound.constant';

/** 歸因列表可篩選欄位（值型別對齊各控制項 v-model）。 */
export interface AttributionFilters {
  /** 傾向（多選 negative/neutral/positive；分別對應情緒分 1-2 / 3 / 4-5）。預設不選＝不篩選。 */
  polarity: string[];
  /** 初判階段（多選）。 */
  stage: string[];
  /** 信心分層（單選）。 */
  tier: string;
  /** 初判模型（多選；attributions.model IN——當前初判維度，任一歸因命中即列出）。 */
  model: string[];
  /** 歸因分類（多選任意層級 code；L1/L2 皆可，後端子樹語義命中）。 */
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
  /** 進線分桶（多選；conversations 專屬直欄）。 */
  bucket: string[];
}

/** 可渲染的篩選欄位鍵（`<AttributionFilterBar>` 的 fields 取值）。 */
export type FilterField = keyof AttributionFilters;

/** 初始篩選（列表初始 / 重置 / 導出草稿種子）。傾向預設不選（＝不篩選）。 */
export const emptyFilters = (): AttributionFilters => ({
  polarity: [],
  stage: [],
  tier: '',
  model: [],
  taxonomy: [],
  hasExternal: '',
  dateRange: [],
  recOid: '',
  prodOid: '',
  orderOid: '',
  bucket: [],
});

/** 深拷貝一份篩選（種子彈窗草稿用，避免與來源共用參照）。 */
export const cloneFilters = (f: AttributionFilters): AttributionFilters => ({
  ...f,
  polarity: [...f.polarity],
  stage: [...f.stage],
  model: [...f.model],
  taxonomy: [...f.taxonomy],
  dateRange: [...f.dateRange],
  bucket: [...f.bucket],
});

/** 有無外部評論選項（''=全部 由 allow-clear 表達）。 */
export const HAS_EXTERNAL_OPTS = [
  { value: 'true', label: '有外部評論' },
  { value: 'false', label: '無外部評論' },
];

/** 傾向篩選選項（順序：負向/中立/正向；label 衍生自 POLARITY_LABELS SSOT，禁再寫死第二份）。 */
export const POLARITY_FILTER_OPTS = ['negative', 'neutral', 'positive'].map((value) => ({
  value,
  label: POLARITY_LABELS[value],
}));
/** 初判階段 / 信心分層選項（自 label 常數衍生，單一真相）。 */
export const STAGE_OPTS = Object.entries(STAGE_LABELS).map(([value, label]) => ({ value, label }));
export const TIER_OPTS = Object.entries(TIER_LABELS).map(([value, label]) => ({ value, label }));
/** 進線分桶篩選選項（conversations 專屬；label 衍生自 BUCKET_LABELS SSOT）。 */
export const BUCKET_FILTER_OPTS = Object.entries(BUCKET_LABELS).map(([value, label]) => ({
  value,
  label,
}));

/** 已套用的篩選項數（計數徽章用；空值不計）。 */
export const countActiveFilters = (f: AttributionFilters): number =>
  (f.polarity.length ? 1 : 0) +
  (f.stage.length ? 1 : 0) +
  (f.tier ? 1 : 0) +
  (f.model.length ? 1 : 0) +
  (f.taxonomy.length ? 1 : 0) +
  (f.hasExternal ? 1 : 0) +
  (f.dateRange.length ? 1 : 0) +
  (f.recOid.trim() ? 1 : 0) +
  (f.prodOid.trim() ? 1 : 0) +
  (f.orderOid.trim() ? 1 : 0) +
  (f.bucket.length ? 1 : 0);

/** 篩選 → getProblems / 導出 API 參數（統一轉換，空值一律 undefined 不送）。
 *  傾向直接按 attributions.polarity 多選篩（正向/中性/負向）。 */
export const filtersToParams = (f: AttributionFilters) => {
  return {
    polarity: f.polarity.length ? f.polarity : undefined,
    stage: f.stage.length ? f.stage : undefined,
    confidenceTier: f.tier || undefined,
    model: f.model.length ? f.model : undefined,
    taxonomy: f.taxonomy.length ? f.taxonomy : undefined,
    hasExternal: f.hasExternal || undefined,
    dateFrom: f.dateRange?.[0] || undefined,
    dateTo: f.dateRange?.[1] || undefined,
    recOid: f.recOid.trim() || undefined,
    prodOid: f.prodOid.trim() || undefined,
    orderOid: f.orderOid.trim() || undefined,
    bucket: f.bucket.length ? f.bucket : undefined,
  };
};
