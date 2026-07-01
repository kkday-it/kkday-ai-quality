// 各來源歸因列表差異化 schema（欄位 / 展開行明細 / 篩選器）——SSOT，AttributionList 依 source 切換整組讀取。
// product_reviews 先完整打樣；其餘 4 來源沿用舊 intake_items 現況，以現行固定欄位當 fallback stub
// （待該來源遷移專屬表時再依 §7 補完整 schema，非本次範圍）。
import type { TableColumnData } from '@arco-design/web-vue';

/** 星等篩選（多選）。 */
export interface ScoreFilterDef {
  type: 'score';
  /** 可選星等清單（如 [1,2,3,4,5]）。 */
  options: number[];
}

/** 商品分類分組篩選（多選；選項來自 config/global/product_vertical.json 動態解析）。 */
export interface CategoryGroupFilterDef {
  type: 'categoryGroup';
}

/** 日期區間篩選（對應後端某個時間欄位，如評論時間或出發日）。 */
export interface DateRangeFilterDef {
  type: 'dateRange';
  /** 篩選作用的欄位語意（後端 date_field 參數）。 */
  field: 'occurred_at' | 'go_date';
  /** 篩選列顯示 label。 */
  label: string;
}

/** 傾向篩選（正向/負向/中性/數據不足；沿用既有「僅看問題」與下拉互動）。 */
export interface PolarityFilterDef {
  type: 'polarity';
}

/** 單一來源可用篩選器（discriminated union，依 type 決定渲染的 UI 與送出的查詢參數）。 */
export type SourceFilterDef =
  | PolarityFilterDef
  | ScoreFilterDef
  | CategoryGroupFilterDef
  | DateRangeFilterDef;

/** 展開行明細單一欄位定義（key 對應 `_enrich_problem` 回傳欄位；缺值防禦式顯示「—」）。 */
export interface ExpandFieldDef {
  /** 後端記錄欄位 key。 */
  key: string;
  /** a-descriptions 顯示 label。 */
  label: string;
  /** 特殊格式化：'datetime' 完整時間 / 'date' 僅日期 / 未指定＝原樣顯示。 */
  format?: 'datetime' | 'date';
}

/** 單一來源的歸因列表 schema：欄位 + 篩選器 + 展開行明細。 */
export interface SourceListSchema {
  columns: TableColumnData[];
  filters: SourceFilterDef[];
  expandFields: ExpandFieldDef[];
}

/** L3 候選（後端 `l3_candidates`：目前僅 code/score；label 保留給未來後端補中文名）。 */
export interface L3Candidate {
  code?: string;
  label?: string;
  score?: number;
}

/**
 * 歸因列表單列（`_enrich_problem` 回傳）。常用欄位具名、其餘走 index signature——
 * 各來源欄位集不同（product_reviews 有 score、conversations 無），故不列窮舉、以 `unknown` 保型別安全
 * （取代 any：動態欄位存取回 unknown，仍受檢查，勝過完全關閉的 any）。
 */
export interface ProblemRow {
  item_id: string;
  polarity?: string;
  confidence_tier?: string;
  l3_candidates?: L3Candidate[];
  [key: string]: unknown;
}

/** product_reviews 打樣欄位：序號欄由 AttributionList 統一前置，此處只列業務欄。 */
const PRODUCT_REVIEWS_COLUMNS: TableColumnData[] = [
  { title: '訂單', dataIndex: 'order_mid' },
  { title: '商品名稱', dataIndex: 'prod_name', ellipsis: true, tooltip: true },
  { title: '傾向', dataIndex: 'polarity', slotName: 'pol' },
  { title: '歸因（L1→L3）', dataIndex: 'attr', slotName: 'attr' },
  { title: '信心', dataIndex: 'confidence' },
  { title: '分層', dataIndex: 'confidence_tier', slotName: 'tier' },
];

/** product_reviews 展開行：評論全文 + 星等 + 時間 + 旅客資訊 + 判決依據，一次看齊。 */
const PRODUCT_REVIEWS_EXPAND_FIELDS: ExpandFieldDef[] = [
  { key: 'content', label: '評論全文' },
  { key: 'score', label: '星等' },
  { key: 'occurred_at', label: '評論時間', format: 'datetime' },
  { key: 'go_date', label: '出發日', format: 'date' },
  { key: 'traveller_type', label: '旅客類型' },
  { key: 'lang', label: '語言' },
  { key: 'member_uuid', label: '會員 UUID' },
  { key: 'pkg_oid', label: '套裝ID' },
  { key: 'problem_summary', label: '問題摘要' },
  { key: 'evidence_quote', label: '判決依據引用' },
  { key: 'reason', label: '判決理由' },
  { key: 'l3_candidates', label: 'L3 候選（top-3）' },
];

const PRODUCT_REVIEWS_FILTERS: SourceFilterDef[] = [
  { type: 'polarity' },
  { type: 'score', options: [1, 2, 3, 4, 5] },
  { type: 'categoryGroup' },
  { type: 'dateRange', field: 'occurred_at', label: '評論時間' },
];

/** 其餘 4 來源 fallback stub：沿用 AttributionList 舊固定欄位，僅傾向篩選（現況 1:1 對等，非新增能力）。 */
const FALLBACK_COLUMNS: TableColumnData[] = [
  { title: '商品ID', dataIndex: 'prod_oid' },
  { title: '商品名稱', dataIndex: 'prod_name', ellipsis: true, tooltip: true },
  { title: '評論 / 內容', dataIndex: 'content', ellipsis: true, tooltip: true },
  { title: '星等', dataIndex: 'score' },
  { title: '評論時間', dataIndex: 'occurred_at', slotName: 'occurred' },
  { title: '出發日', dataIndex: 'go_date', slotName: 'godate' },
  { title: '訂單', dataIndex: 'order_mid' },
  { title: '傾向', dataIndex: 'polarity', slotName: 'pol' },
  { title: '歸因（L1→L3）', dataIndex: 'attr', slotName: 'attr' },
  { title: '信心', dataIndex: 'confidence' },
  { title: '分層', dataIndex: 'confidence_tier', slotName: 'tier' },
];
const FALLBACK_EXPAND_FIELDS: ExpandFieldDef[] = [
  { key: 'content', label: '內容全文' },
  { key: 'problem_summary', label: '問題摘要' },
  { key: 'evidence_quote', label: '判決依據引用' },
  { key: 'reason', label: '判決理由' },
];
const FALLBACK_FILTERS: SourceFilterDef[] = [{ type: 'polarity' }];
const FALLBACK_SCHEMA: SourceListSchema = {
  columns: FALLBACK_COLUMNS,
  filters: FALLBACK_FILTERS,
  expandFields: FALLBACK_EXPAND_FIELDS,
};

/** source code → 該來源歸因列表 schema；未註冊來源一律回退 `FALLBACK_SCHEMA`。 */
export const SOURCE_LIST_SCHEMAS: Record<string, SourceListSchema> = {
  product_reviews: {
    columns: PRODUCT_REVIEWS_COLUMNS,
    filters: PRODUCT_REVIEWS_FILTERS,
    expandFields: PRODUCT_REVIEWS_EXPAND_FIELDS,
  },
};

/**
 * 取某來源的歸因列表 schema；未註冊（其餘 4 來源尚未遷移專屬表）回退 fallback stub。
 * @param source 來源 code
 * @returns 該來源 columns/filters/expandFields
 */
export function schemaFor(source: string): SourceListSchema {
  return SOURCE_LIST_SCHEMAS[source] ?? FALLBACK_SCHEMA;
}
