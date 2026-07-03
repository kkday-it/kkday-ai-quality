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

/** 商品垂直分類篩選（多選；選項來自 rule_code=product_vertical 的 active 版本動態解析）。 */
export interface ProductVerticalFilterDef {
  type: 'productVertical';
}

/** 日期區間篩選（對應後端某個時間欄位，如評論時間或出發日）。 */
export interface DateRangeFilterDef {
  type: 'dateRange';
  /** 篩選作用的欄位語意（後端 date_field 參數）。 */
  field: 'occurred_at' | 'go_date';
  /** 篩選列顯示 label。 */
  label: string;
}

/** 傾向篩選（正向/負向/中性/傾向不明；沿用既有「僅看問題」與下拉互動）。 */
export interface PolarityFilterDef {
  type: 'polarity';
}

/** 單一來源可用篩選器（discriminated union，依 type 決定渲染的 UI 與送出的查詢參數）。 */
export type SourceFilterDef =
  PolarityFilterDef | ScoreFilterDef | ProductVerticalFilterDef | DateRangeFilterDef;

/** 展開行明細單一欄位定義（key 對應 `_enrich_problem` 回傳欄位；缺值防禦式顯示「—」）。 */
export interface ExpandFieldDef {
  /** 後端記錄欄位 key。 */
  key: string;
  /** a-descriptions 顯示 label。 */
  label: string;
  /** 特殊格式化：'datetime' 完整時間 / 'date' 僅日期 / 未指定＝原樣顯示。 */
  format?: 'datetime' | 'date';
  /** a-descriptions 跨欄數（:column=4 版面控制每列排布；預設 1）。 */
  span?: number;
  /** 特殊渲染：'rate' 星等 a-rate / 'traveller' 旅客類型映射。 */
  kind?: 'rate' | 'traveller';
}

/** 展開行分組：每組渲染一個帶標題的 a-descriptions，讓明細分區更顯眼。 */
export interface ExpandGroupDef {
  /** 分組標題（a-descriptions title；空＝無標題）。 */
  title?: string;
  /** 該組 a-descriptions 欄數（預設 4）。 */
  column?: number;
  /** 組內欄位。 */
  fields: ExpandFieldDef[];
}

/** 單一來源的歸因列表 schema：欄位 + 篩選器 + 展開行分組明細。 */
export interface SourceListSchema {
  columns: TableColumnData[];
  filters: SourceFilterDef[];
  expandGroups: ExpandGroupDef[];
}

/** L3 候選（後端 `l3_candidates`：目前僅 code/score；label 保留給未來後端補中文名）。 */
export interface L3Candidate {
  code?: string;
  label?: string;
  score?: number;
}

/** 單條歸因分類（後端 `_attribution_of`：一則評論 1:N 多歸因，各帶 L1-L3/信心/分層/階段）。 */
export interface Attribution {
  finding_id?: string;
  l1_domain?: string;
  l1_label?: string;
  l2_code?: string;
  l2_label?: string;
  l3_code?: string;
  l3_label?: string;
  confidence?: number;
  confidence_tier?: string;
  judgment_stage?: string;
  recommended_action?: string;
  polarity?: string;
  problem_summary?: string;
  reason?: string;
  is_primary?: boolean;
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
  source_id?: string; // 該來源特徵 id（product_reviews→rec_oid…；選取/導出業務身分）
  // ── 一列一 review（後端 _paged_fanout 附）：多歸因收進 attributions 陣列，右側單欄堆疊呈現 ──
  _group?: string; // 該 review 的特徵 id（source_id；前端 rowKey / expand key）
  _seq?: number; // review 在本頁的序號（#seq 顯示）
  attributions?: Attribution[]; // 該 review 的多條歸因（0＝未判，右欄顯示「—」）
  [key: string]: unknown;
}

/**
 * product_reviews 主列欄位：只放「判決數據」（訂單 / 傾向 / 歸因 / 信心 / 分層）；
 * 原始數據（商品名稱、評論全文等）移入展開行，主列聚焦判決結果。序號欄由 AttributionList 統一前置。
 */
const PRODUCT_REVIEWS_COLUMNS: TableColumnData[] = [
  { title: '訂單', dataIndex: 'order_mid' },
  // 評論時間 / 信心走表頭點擊排序（Arco sortable）→ 後端 sort_by=occurred_at / confidence；
  // 其餘欄後端無對應排序欄故不開。評論時間為可排序欄故留主列（不再進展開，避免重複）。
  {
    title: '評論時間',
    dataIndex: 'occurred_at',
    slotName: 'occurred',
    sortable: { sortDirections: ['ascend', 'descend'] },
  },
  { title: '傾向', dataIndex: 'polarity', slotName: 'pol' },
  { title: '歸因（L1→L3）', dataIndex: 'attr', slotName: 'attr' },
  {
    title: '信心',
    dataIndex: 'confidence',
    slotName: 'conf',
    sortable: { sortDirections: ['ascend', 'descend'] },
  },
  { title: '分層', dataIndex: 'confidence_tier', slotName: 'tier' },
  { title: '判決階段', dataIndex: 'judgment_stage', slotName: 'stage' },
];

/**
 * product_reviews 展開行分組（每組一個帶標題 a-descriptions，:column=4，span 合計 4）：
 * 商品 / 評論 / 旅客 / 判決 四區，分區呈現更顯眼。
 */
const PRODUCT_REVIEWS_EXPAND_GROUPS: ExpandGroupDef[] = [
  {
    title: '商品資訊',
    column: 4,
    fields: [
      { key: 'prod_oid', label: '商品OID', span: 1 },
      { key: 'prod_name', label: '商品名稱', span: 2 },
      { key: 'lang', label: '商品語系', span: 1 }, // lang_code＝商品語系（非導覽），與商品OID同列
      { key: 'pkg_oid', label: '方案OID', span: 1 },
      { key: 'package_name', label: '方案名稱', span: 2 },
      { key: 'product_category_main', label: '商品分類', span: 1 },
    ],
  },
  {
    title: '評論資訊',
    column: 4,
    fields: [
      { key: 'source_record_id', label: '評論ID', span: 1 },
      { key: 'title', label: '評論標題', span: 1 },
      { key: 'score', label: '評論星等', span: 1, kind: 'rate' },
      { key: 'occurred_at', label: '評論時間', span: 1, format: 'datetime' },
      // 評論內容 ‖ 問題摘要 並列：左＝完整原文、右＝主歸因標出的痛點片段（原判決資訊區的
      // 依據/判決理由已移除——依據＝問題摘要複製、判決理由永遠空，見 prejudge 產出）。
      { key: 'content', label: '評論內容', span: 2 },
      { key: 'problem_summary', label: '問題摘要', span: 2 },
    ],
  },
  {
    title: '旅客資訊',
    column: 4,
    fields: [
      { key: 'member_uuid', label: '會員UUID', span: 2 }, // 與旅客類型左右均分
      { key: 'traveller_type', label: '旅客類型', span: 2, kind: 'traveller' },
    ],
  },
];

// 星等改為僅在展開明細顯示、不作列表篩選（依需求移除 score 篩選器；排序仍可用星等）。
// 商品垂直分類已改為規則配置頁的全局開關（跨列表/縱覽共用），不再是列表本地篩選。
const PRODUCT_REVIEWS_FILTERS: SourceFilterDef[] = [
  { type: 'polarity' },
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
const FALLBACK_EXPAND_GROUPS: ExpandGroupDef[] = [
  {
    column: 1,
    fields: [
      { key: 'content', label: '內容全文' },
      { key: 'problem_summary', label: '問題摘要' }, // 依據/判決理由已移除（重複/永遠空）
    ],
  },
];
const FALLBACK_FILTERS: SourceFilterDef[] = [{ type: 'polarity' }];
const FALLBACK_SCHEMA: SourceListSchema = {
  columns: FALLBACK_COLUMNS,
  filters: FALLBACK_FILTERS,
  expandGroups: FALLBACK_EXPAND_GROUPS,
};

/** source code → 該來源歸因列表 schema；未註冊來源一律回退 `FALLBACK_SCHEMA`。 */
export const SOURCE_LIST_SCHEMAS: Record<string, SourceListSchema> = {
  product_reviews: {
    columns: PRODUCT_REVIEWS_COLUMNS,
    filters: PRODUCT_REVIEWS_FILTERS,
    expandGroups: PRODUCT_REVIEWS_EXPAND_GROUPS,
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
