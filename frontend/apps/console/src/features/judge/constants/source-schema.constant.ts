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

/** 傾向篩選（正向/負向/中性/傾向不明；下拉單選）。 */
export interface PolarityFilterDef {
  type: 'polarity';
}

/** 判決階段篩選（多選；選項來自 STAGE_LABELS，值 unjudged/judged/pending_review/pending_data/insufficient）。 */
export interface StageFilterDef {
  type: 'stage';
}

/** 信心分層篩選（單選；選項來自 TIER_LABELS，值 auto_accept/jury/needs_review/hold）。 */
export interface TierFilterDef {
  type: 'tier';
}

/** L1 歸因域篩選（單選；選項為該來源已判資料 distinct，經 getL1Domains 動態載入）。 */
export interface L1DomainFilterDef {
  type: 'l1Domain';
}

/** 單一來源可用篩選器（discriminated union，依 type 決定渲染的 UI 與送出的查詢參數）。 */
export type SourceFilterDef =
  | PolarityFilterDef
  | ScoreFilterDef
  | StageFilterDef
  | TierFilterDef
  | L1DomainFilterDef
  | ProductVerticalFilterDef
  | DateRangeFilterDef;

/** 單一來源的歸因列表 schema：欄位 + 篩選器（展開行已廢除，關聯明細改複合欄位平鋪主列）。 */
export interface SourceListSchema {
  columns: TableColumnData[];
  filters: SourceFilterDef[];
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
  /** 人工覆核 status（confirmed/dismissed/fixed）——後端 _attribution_of 回傳；覆核徽章用。 */
  status?: string;
  /** 人工標註真值分類 true_label（正確 L1 域 code）——標真值功能用。 */
  true_label?: string;
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
 * 統一主列欄位（**全 5 反饋來源共用**，無展開行，複合欄合併同類資訊）。
 * 排列原則：**源數據在前，判決數據在後**。序號欄由 AttributionList 統一前置。
 *   1. 反饋內容（星等+傾向+標題+內容全文+ID·時間，可按反饋時間排序）
 *   2. 關聯資料（訂單→商品→方案→供應商→旅客，各段小標籤；缺欄防禦式「—」，故各來源皆適用）
 *   3. 判決歸因（L1→L3 + 摘要 + 信心/分層/階段 + per-歸因覆核，每條一塊）
 *   4. 操作（整列級 歸因/重判 + 查看詳情）
 * 複合欄（review/context/verdict/actions）以 slotName 客製渲染，欄位 key 皆 `_enrich_problem` 現成
 * （非該來源的欄位回空 → 顯示「—」，達成「盡可能統一」的優雅降級）。
 */
const COMPOSITE_COLUMNS: TableColumnData[] = [
  {
    title: '反饋內容',
    dataIndex: 'occurred_at',
    slotName: 'review',
    width: 320,
    sortable: { sortDirections: ['ascend', 'descend'] },
  },
  { title: '關聯資料', dataIndex: 'order_mid', slotName: 'context', width: 300 },
  {
    title: '判決歸因',
    dataIndex: 'confidence',
    slotName: 'verdict',
    width: 260,
    sortable: { sortDirections: ['ascend', 'descend'] },
  },
  { title: '操作', slotName: 'actions', width: 132, fixed: 'right' },
];

/** 有星等欄的來源（product_reviews=rec_scores / freshdesk=st_survey_rating / app_feedback=score）→ 才給星等篩選。 */
const RATING_SOURCES = new Set(['product_reviews', 'freshdesk_tickets', 'app_feedback']);

/** 共用篩選（各來源皆適用，落 judgments.data 或時間欄）：傾向 / 判決階段 / 信心分層 / L1域 / 日期區間。 */
const BASE_FILTERS: SourceFilterDef[] = [
  { type: 'polarity' },
  { type: 'stage' },
  { type: 'tier' },
  { type: 'l1Domain' },
  { type: 'dateRange', field: 'occurred_at', label: '反饋時間' },
];

/** 組某來源的篩選：共用集 + 有星等者加星等（插在階段後、信心分層前）。 */
function filtersFor(source: string): SourceFilterDef[] {
  if (!RATING_SOURCES.has(source)) return BASE_FILTERS;
  const scoreFilter: SourceFilterDef = { type: 'score', options: [1, 2, 3, 4, 5] };
  return [BASE_FILTERS[0], BASE_FILTERS[1], scoreFilter, ...BASE_FILTERS.slice(2)];
}

/** 5 反饋來源皆用統一複合欄；差異只在星等篩選（有評分欄者才有）。 */
const _SOURCES = ['product_reviews', 'conversations', 'freshdesk_tickets', 'app_feedback', 'mixpanel_tracker'];
export const SOURCE_LIST_SCHEMAS: Record<string, SourceListSchema> = Object.fromEntries(
  _SOURCES.map((s) => [s, { columns: COMPOSITE_COLUMNS, filters: filtersFor(s) }]),
);

/** 未註冊來源回退：同一套統一複合欄 + 共用篩選（無星等）。 */
const FALLBACK_SCHEMA: SourceListSchema = { columns: COMPOSITE_COLUMNS, filters: BASE_FILTERS };

/**
 * 取某來源的歸因列表 schema；5 來源皆註冊為統一複合欄，未知來源回退 FALLBACK。
 * @param source 來源 code
 * @returns 該來源 columns/filters
 */
export function schemaFor(source: string): SourceListSchema {
  return SOURCE_LIST_SCHEMAS[source] ?? FALLBACK_SCHEMA;
}
