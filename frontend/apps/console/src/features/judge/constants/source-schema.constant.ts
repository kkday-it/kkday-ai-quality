// 各來源歸因列表差異化 schema（欄位 / 篩選器 / 顯示配置）——SSOT，AttributionList 依 source 切換整組讀取。
// 5 來源共用統一複合欄 + 共用篩選；顯示差異（內容欄標籤 / 對話模式 / 關聯資料段落 / 精確查詢名詞）
// 依 SOURCE_DISPLAY 客製（如 conversations＝進線對話輪次 + 進線屬性段，非單純「反饋內容」語義）。
import type { TableColumnData } from '@arco-design/web-vue';
import { SOURCES } from './source.constant';

/** 日期區間篩選（對應後端某個時間欄位，如評論時間或出發日）。 */
export interface DateRangeFilterDef {
  type: 'dateRange';
  /** 篩選作用的欄位語意（後端 date_field 參數）。 */
  field: 'occurred_at' | 'go_date';
  /** 篩選列顯示 label。 */
  label: string;
}

/** 傾向篩選（正向/中性/負向 多選；底層映射情緒分區間 4-5/3/1-2，預設不選＝不篩選）。 */
export interface PolarityFilterDef {
  type: 'polarity';
}

/** 初判階段篩選（多選；選項來自 STAGE_LABELS，值 unjudged/judged/pending_review/pending_data）。 */
export interface StageFilterDef {
  type: 'stage';
}

/** 信心分層篩選（單選；選項來自 TIER_LABELS，值 auto_accept/jury/needs_review/human）。
 *  下拉由 TIER_LABELS 動態產生，故 SSOT 加了 human 之後這裡自動多一個選項，無需改碼。 */
export interface TierFilterDef {
  type: 'tier';
}


/** 初判模型篩選（多選；選項來自 /api/attribution-history/models，值 attributions.model 當前初判維度）。 */
export interface ModelFilterDef {
  type: 'model';
}

/** 歸因分類篩選（a-cascader L1→L2 級聯複選；選項來自 getTaxonomyCascade，任意層級 code 子樹語義）。 */
export interface TaxonomyFilterDef {
  type: 'taxonomy';
}

/** 有無外部評論篩選（單選；全部/有/無——評論系統融合欄 sentiment/free_tag 是否有值）。 */
export interface HasExternalFilterDef {
  type: 'hasExternal';
}

/** 人工介入狀態篩選（單選；AI 原判／已人工介入／有待審建議——全 5 來源皆適用）。 */
export interface HumanStateFilterDef {
  type: 'humanState';
}

/** 進線分桶篩選（多選；conversations 專屬直欄，選項來自 BUCKET_LABELS）。 */
export interface BucketFilterDef {
  type: 'bucket';
}

/** 單一來源可用篩選器（discriminated union，依 type 決定渲染的 UI 與送出的查詢參數）。
 *  ⚠️ 全局商品垂直分類（bd_tag 維度）不在此 per-source schema 內——它是跨來源、不受 source 切換
 *  影響的獨立工具列控件（見 verticalFilter.store），非本檔案管的「來源專屬篩選器」。 */
export type SourceFilterDef =
  | PolarityFilterDef
  | StageFilterDef
  | TierFilterDef
  | ModelFilterDef
  | TaxonomyFilterDef
  | HasExternalFilterDef
  | HumanStateFilterDef
  | DateRangeFilterDef
  | BucketFilterDef;

/** 可渲染的資料段落（由 RecordContextPanel 統一渲染；依來源裁剪，並分派到「關聯資料」欄或
 *  「反饋補充」區塊——見 SourceListSchema 的 contextSections / supplementSections）。 */
export type ContextSection =
  'order' | 'product' | 'package' | 'supplier' | 'traveller' | 'inbound' | 'org';

/** 資料段落左側小標籤樣式（列表欄的「原文」/「補充」與 RecordContextPanel compact 版型
 *  共用，確保各段標籤欄等寬對齊；跨檔共用故收斂於此，勿再各處手抄同一串 utility class）。 */
export const SECTION_LABEL_CLASS =
  'flex min-w-[3rem] shrink-0 items-center justify-center self-stretch whitespace-nowrap rounded bg-[var(--color-fill-2)] px-1.5 py-0.5 text-center text-[11px] font-medium text-[var(--color-text-2)]';

/** 全部資料段落（RecordContextPanel 未指定 sections 時的預設＝不裁剪，靠各欄位自身 v-if 決定）。 */
export const ALL_CONTEXT_SECTIONS: ContextSection[] = [
  'order',
  'product',
  'package',
  'supplier',
  'traveller',
  'inbound',
  'org',
];

/** 單一來源的歸因列表 schema：欄位 + 篩選器 + 顯示差異化（展開行已廢除，關聯明細改複合欄位平鋪主列）。 */
export interface SourceListSchema {
  columns: TableColumnData[];
  filters: SourceFilterDef[];
  /** 反饋內容欄左側標籤（如 反饋內容／進線對話——來源語義各異，禁寫死單一名詞）。 */
  contentLabel: string;
  /** 精確查詢 id 的業務名詞（placeholder 組「{idNoun} {natural_key} 如 1,2,3」用）。 */
  idNoun: string;
  /** 內容渲染模式：text＝原樣全文；dialogue＝按 [ROLE]: 前綴解析成對話輪次（解析失敗 fallback 原樣）。 */
  contentMode: 'text' | 'dialogue';
  /** 「關聯資料」欄段落白名單＝訂單/商品/方案等**關聯實體**（固定順序渲染；該來源恆空的段落
   *  不列，避免整欄「—」噪音）。 */
  contextSections: ContextSection[];
  /** 反饋內容欄「補充」區塊的段落白名單＝關於**這則反饋自身**的附加屬性（如進線的分桶/行程階段/
   *  處理方/訊息數）。與外部評論融合維度（ext_sentiment/ext_free_tag）同置於該區塊；無此類屬性
   *  的來源給空陣列。 */
  supplementSections: ContextSection[];
}

/** 歸因分類層（L1/L2 共用形狀）。 */
export interface AttributionLevel {
  code?: string;
  label?: string;
}

/** 歸因信心（value=最終校準後 / raw=LLM 原始 / tier=分層）。 */
export interface AttributionConfidence {
  value?: number;
  raw?: number;
  tier?: string;
}

/** 歸因初判內容（摘要 / 佐證原文 / 建議行動）。 */
export interface AttributionContent {
  /** 表格顯示用摘要＝繁中（zh-tw）字串（後端由 summary_langs 取出，前端直接用）。 */
  summary?: string;
  /** 全語系摘要 map（語系碼→簡明摘要，如 {'zh-tw':…, ja:…}）；詳情/多語用，去重可能只有 zh-tw。 */
  summary_langs?: Record<string, string>;
  evidence?: string;
  action?: string;
}

/**
 * 單條歸因分類（後端 `attribution_dto`：一則評論 1:N 多歸因，乾淨巢狀物件）。
 * 一條形狀貫穿 DB(typed 欄)→API→前端；L1-L2/信心/內容各為分組物件。
 */
export interface Attribution {
  /** 歸因流水號主鍵（serial）；同一則反饋的多條歸因靠它區分。 */
  attribution_oid?: number;
  polarity?: string;
  /** 情緒分 1-5（與 polarity 同段輸出：負 1-2 / 中 3 / 正 4-5）。 */
  sentiment_score?: number;
  /** 初判階段（judged/pending_review/pending_data）。 */
  stage?: string;
  l1?: AttributionLevel;
  l2?: AttributionLevel;
  confidence?: AttributionConfidence;
  content?: AttributionContent;
  /** 負責單位（後端自 l1 域 rule _meta.owner_role 派生；業務未填時為空字串，不顯示標籤）。 */
  owner?: string;
  /** 初判模型（如 gpt-5-mini；stub＝假判）——初判溯源標籤用。 */
  model?: string;
  /** 多歸因中的主歸因旗標。 */
  is_primary?: boolean;
  /** 系統是否自動採納（信心達 auto_accept 門檻）。 */
  is_auto_accepted?: boolean;
  /** 現值來源（後端派生的顯示 SSOT）：`human`＝人工糾正或新增，顯示修改者取代 model。 */
  origin?: 'ai' | 'human';
  /** 人工手動新增（AI 未產出、由人補上）。 */
  is_manual_created?: boolean;
  /** AI 產出後由人工改過值。 */
  is_human_corrected?: boolean;
  /** 糾正者（無 SSO 時為 system）。 */
  corrected_by?: string | null;
  /** 糾正時間（ISO）。 */
  corrected_at?: string | null;
  /** 最近一次糾正／刪除的理由。 */
  correction_reason?: string | null;
  /** 人工複審狀態：unreviewed / confirmed（確認 AI 判對）/ corrected（已糾正）。 */
  review_status?: string;
}

/**
 * 歸因列表單列（`_enrich_problem` 回傳）。常用欄位具名、其餘走 index signature——
 * 各來源欄位集不同（reviews 有 score、conversations 無），故不列窮舉、以 `unknown` 保型別安全
 * （取代 any：動態欄位存取回 unknown，仍受檢查，勝過完全關閉的 any）。
 */
export interface ProblemRow {
  item_id: string;
  polarity?: string; // 列級傾向（列樣式；歸因詳情走 attributions[]）
  source_id?: string; // 該來源特徵 id（reviews→rec_oid…；選取/導出業務身分）
  // ── 一列一 review（後端 _paged_fanout 附）：多歸因收進 attributions 陣列，右側單欄堆疊呈現 ──
  _group?: string; // 該 review 的特徵 id（source_id；前端 rowKey / expand key）
  _seq?: number; // review 在本頁的序號（#seq 顯示）
  attributions?: Attribution[]; // 該 review 的存活歸因（不含人工標記為誤判的列）
  suggestion_count?: number; // 待審 LLM 建議數（人工託管的反饋重新初判後產生；0＝無）
  /**
   * 判定狀態（服務端由兩個 SQL 計數派生，**前端不要再用 attributions.length 自己推**）：
   * `judged` 有存活歸因｜`dismissed` 判過但歸因全被人工標記為 AI 誤判｜`unjudged` 從未判過。
   * `dismissed` 是關鍵的第三態：這種列的 `attributions` 是空陣列，但它**不是**未初判——
   * 少了這個狀態，畫面會顯示「未初判」而批量初判卻不會撈它，兩個數字互相矛盾。
   */
  judge_state?: 'judged' | 'dismissed' | 'unjudged';
  dismissed_count?: number; // 被人工標記為 AI 誤判的歸因數（judge_state='dismissed' 時 >0）
  /** 這則反饋的備註數（整則 + 面向合計）。0＝無，列上不顯示徽記。 */
  note_count?: number;
  [key: string]: unknown;
}

/**
 * 統一主列欄位（**全 5 反饋來源共用**，無展開行，複合欄合併同類資訊）。
 * 排列原則：**源數據在前，初判數據在後**。序號欄由 AttributionList 統一前置。
 *   1. 反饋內容（兩區塊：「原文」＝星等+傾向+標題+內容全文+ID·時間；「補充」＝該反饋自身的附加
 *      屬性，見 `supplementSections` 與外部評論融合維度。可按反饋時間排序）
 *   2. 關聯資料（訂單→商品→方案→供應商→旅客等關聯實體，各段小標籤；缺欄防禦式「—」，各來源皆適用）
 *   3. 判決歸因（L1→L2 + 摘要 + 信心/分層/階段 + per-歸因判決，每條一塊）
 *   4. 操作（整列級 歸因/重新初判 + 查看詳情）
 * 複合欄（review/context/verdict/actions）以 slotName 客製渲染，欄位 key 皆 `_enrich_problem` 現成
 * （非該來源的欄位回空 → 顯示「—」，達成「盡可能統一」的優雅降級）。
 */
const COMPOSITE_COLUMNS: TableColumnData[] = [
  {
    title: '反饋內容（時間）', // 闊號＝排序依據：此欄可排序，依 occurred_at 反饋時間
    dataIndex: 'occurred_at',
    slotName: 'review',
    width: 340,
    sortable: { sortDirections: ['ascend', 'descend'] },
  },
  {
    title: '關聯資料',
    dataIndex: 'order_mid',
    slotName: 'context',
    width: 300,
  },
  {
    title: '判決歸因（信心度）', // 闊號＝排序依據：此欄可排序，依該 review 各歸因最大 confidence 信心度
    dataIndex: 'confidence',
    slotName: 'verdict',
    width: 260,
    sortable: { sortDirections: ['ascend', 'descend'] },
  },
  // 操作欄按分類分四組（反饋／初判／判決／人工），每組兩顆帶 icon 的四字按鈕。
  //
  // **寬度模型：112 是「並排 vs 堆疊」的切換點，不是裁切下限。**
  // `.act-group` 帶 `flex-wrap`（見 AttributionList 的 style），所以這一欄**不可能被裁切**——
  // 不夠寬就換行。於是宣告寬不再需要保守下限，改為挑一個「在常見視窗下呈現想要的形態」的值：
  //
  //   實測（2048px 容器、各欄一致拉伸 1.67×）：
  //     一組並排需 148px（icon+4字 ×2 + gap6×2 + 分隔點4）、單顆需 66px
  //     宣告 112 → 渲染 187 → 可用 155 ≥ 148 → **寬螢幕維持四行並排**
  //     宣告 112 → 窄視窗退回比例 1.0 → 可用 80 ≥ 66 → **自動堆疊成七行，仍不裁切**
  //
  // ⚠️ 曾一路加寬到 180（→ 寬螢幕渲染 301、留白 152px）。原因是當時沒有 flex-wrap，
  // 只能靠「寧可留白也不要靜默裁切」的保守下限硬撐。加了換行之後那個取捨消失了。
  //
  // 列高不是限制：整列高度由「關聯資料」欄決定（實測 315px），而本欄內容並排時 79px、
  // 堆疊成七行也只有 151px——變高完全不影響版面。
  { title: '操作', slotName: 'actions', width: 112, fixed: 'right' },
];

/** 共用篩選（各來源皆適用）：傾向 / 初判階段 / 信心分層 / 初判模型 / 歸因分類 / 日期區間 / 人工介入狀態。 */
const BASE_FILTERS: SourceFilterDef[] = [
  { type: 'polarity' },
  { type: 'stage' },
  { type: 'tier' },
  { type: 'model' },
  { type: 'taxonomy' },
  { type: 'dateRange', field: 'occurred_at', label: '反饋時間' },
  // 人工介入狀態：全 5 來源皆適用（糾正與待審建議不分來源）
  { type: 'humanState' },
];

/** 組某來源的篩選：共用集（+ reviews 專屬「有無外部評論」；+ conversations 專屬「分桶／商品垂直分類」）。 */
function filtersFor(source: string): SourceFilterDef[] {
  const base = [...BASE_FILTERS];
  // 有無外部評論：僅 reviews 有融合欄（sentiment/free_tag）
  if (source === 'reviews') base.push({ type: 'hasExternal' });
  // 分桶：僅 conversations 有直欄
  if (source === 'conversations') base.push({ type: 'bucket' });
  return base;
}

/** 關聯資料欄預設段落（訂單→商品→方案→供應商→旅客；reviews 等評論形來源全段適用）。 */
const DEFAULT_CONTEXT_SECTIONS: ContextSection[] = [
  'order',
  'product',
  'package',
  'supplier',
  'traveller',
];

/** 各來源顯示差異化配置（欄位/篩選共用，僅標籤/內容模式/關聯段落依來源語義客製）。 */
const SOURCE_DISPLAY: Record<
  string,
  Pick<
    SourceListSchema,
    'contentLabel' | 'idNoun' | 'contentMode' | 'contextSections' | 'supplementSections'
  >
> = {
  // 'org'＝組織分工（vertical/BD TAG/PM，bd_tag 系統）：reviews 與 conversations 皆有值，
  // 獨立於 'inbound'（bucket/行程階段/處理方/客服標籤/訊息數，conversations 專屬）之外的共用段落。
  reviews: {
    contentLabel: '反饋內容',
    idNoun: '評論',
    contentMode: 'text',
    contextSections: [...DEFAULT_CONTEXT_SECTIONS, 'org'],
    supplementSections: [], // 外部評論融合維度由反饋補充區塊直接渲染，無額外段落
  },
  // 進線＝客服對話：無方案/旅客欄（恆空不列），關聯資料只留關聯實體 + 組織分工；進線自身屬性
  // （分桶/行程階段/處理方/訊息數）改歸「反饋補充」區塊；內容按 [ROLE]: 解析輪次
  conversations: {
    contentLabel: '進線對話',
    idNoun: '進線',
    contentMode: 'dialogue',
    contextSections: ['order', 'product', 'supplier', 'org'],
    supplementSections: ['inbound'],
  },
  freshdesk_tickets: {
    contentLabel: '工單內容',
    idNoun: '工單',
    contentMode: 'text',
    contextSections: DEFAULT_CONTEXT_SECTIONS,
    supplementSections: [],
  },
  app_feedback: {
    contentLabel: '反饋內容',
    idNoun: '反饋',
    contentMode: 'text',
    contextSections: DEFAULT_CONTEXT_SECTIONS,
    supplementSections: [],
  },
  mixpanel_tracker: {
    contentLabel: '反饋內容',
    idNoun: '反饋',
    contentMode: 'text',
    contextSections: DEFAULT_CONTEXT_SECTIONS,
    supplementSections: [],
  },
};

/** 未知來源的顯示配置回退（泛稱反饋 + 全段落）。 */
const FALLBACK_DISPLAY: Pick<
  SourceListSchema,
  'contentLabel' | 'idNoun' | 'contentMode' | 'contextSections' | 'supplementSections'
> = {
  contentLabel: '反饋內容',
  idNoun: '反饋',
  contentMode: 'text',
  contextSections: DEFAULT_CONTEXT_SECTIONS,
  supplementSections: [],
};

/** 5 反饋來源皆用統一複合欄 + 共用篩選；顯示差異（標籤/內容模式/關聯段落）依 SOURCE_DISPLAY。 */
const _SOURCES = [
  'reviews',
  'conversations',
  'freshdesk_tickets',
  'app_feedback',
  'mixpanel_tracker',
];
export const SOURCE_LIST_SCHEMAS: Record<string, SourceListSchema> = Object.fromEntries(
  _SOURCES.map((s) => [
    s,
    {
      columns: COMPOSITE_COLUMNS,
      filters: filtersFor(s),
      ...(SOURCE_DISPLAY[s] ?? FALLBACK_DISPLAY),
    },
  ]),
);

/** 未註冊來源回退：同一套統一複合欄 + 共用篩選 + 泛稱顯示。 */
const FALLBACK_SCHEMA: SourceListSchema = {
  columns: COMPOSITE_COLUMNS,
  filters: BASE_FILTERS,
  ...FALLBACK_DISPLAY,
};

/**
 * 取某來源的歸因列表 schema；5 來源皆註冊為統一複合欄，未知來源回退 FALLBACK。
 * @param source 來源 code
 * @returns 該來源 columns/filters
 */
export function schemaFor(source: string): SourceListSchema {
  return SOURCE_LIST_SCHEMAS[source] ?? FALLBACK_SCHEMA;
}

/**
 * 精確查詢輸入框 placeholder（業務名詞 + 該來源 natural_key）：如
 * reviews→「評論 rec_oid 如 1,2,3」、conversations→「進線 session_oid 如 1,2,3」。
 * 後端 rec_oid 參數實際按各來源 natural_key 查（_shared.py），placeholder 與之對齊避免誤導。
 * @param source 來源 code
 * @returns 該來源的 recOid 篩選 placeholder
 */
export function idPlaceholderFor(source: string): string {
  const naturalKey = SOURCES.find((s) => s.value === source)?.natural_key || 'id';
  return `${schemaFor(source).idNoun} ${naturalKey} 如 1,2,3`;
}
