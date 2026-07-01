// 歸因領域 API：統一問題列表 + 即時匯總 + 初判歸因批量任務（選模型 + 進度輪詢）。
import { BASE, getToken, j } from './http.api';

/** 統一問題列表查詢參數（source/judged/polarity 既有；scores/productVerticals/日期區間為新增篩選）。 */
export interface GetProblemsParams {
  source?: string;
  judged?: boolean;
  polarity?: string;
  /** 星等篩選（多選，IN 語意；僅有 score 欄的來源如 product_reviews 有效）。 */
  scores?: number[];
  /** 商品垂直分類名（多選；後端展開為 CATEGORY 代碼清單再篩，分組清單 server-authoritative）。 */
  productVerticals?: string[];
  /** 日期區間起（含，'YYYY-MM-DD'）。 */
  dateFrom?: string;
  /** 日期區間迄（含，'YYYY-MM-DD'）。 */
  dateTo?: string;
  limit?: number;
  offset?: number;
}

/** 統一問題列表（intake + 歸因 join）。judged=true 僅已歸因。 */
export const getProblems = (params: GetProblemsParams = {}) => {
  const q = new URLSearchParams();
  if (params.source) q.set('source', params.source);
  if (params.judged !== undefined) q.set('judged', String(params.judged));
  if (params.polarity) q.set('polarity', params.polarity);
  if (params.scores?.length) q.set('scores', params.scores.join(','));
  if (params.productVerticals?.length) q.set('product_verticals', params.productVerticals.join(','));
  if (params.dateFrom) q.set('date_from', params.dateFrom);
  if (params.dateTo) q.set('date_to', params.dateTo);
  q.set('limit', String(params.limit ?? 2000));
  q.set('offset', String(params.offset ?? 0));
  return j(`${BASE}/problems?${q.toString()}`);
};

/** 問題即時匯總（來源 / 域 / 信心分層 分佈）。 */
export const getProblemsSummary = () => j(`${BASE}/problems/summary`);

/** 導出 CSV（POST·item_ids 放 body 避免 URL 過長 431）→ 回 Blob 供前端下載。 */
export const exportProblems = async (p: {
  source?: string;
  polarity?: string;
  judged?: boolean;
  item_ids?: string[];
  /** 星等篩選（多選，IN 語意）。 */
  scores?: number[];
  /** 商品垂直分類名（多選；後端展開為 CATEGORY 代碼清單）。 */
  product_verticals?: string[];
  /** 日期區間起（含，'YYYY-MM-DD'）。 */
  date_from?: string;
  /** 日期區間迄（含，'YYYY-MM-DD'）。 */
  date_to?: string;
}): Promise<Blob> => {
  const token = getToken();
  const res = await fetch(`${BASE}/problems/export`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(p),
  });
  if (!res.ok) throw new Error(`導出失敗 ${res.status}`);
  return res.blob();
};

/** 啟動初判歸因批量任務（選擇驅動：item_ids 複選 / scope=all 全部未判 + 指定模型）→ {job_id, total, model}。 */
export const startPrejudge = (body: {
  item_ids?: string[];
  source?: string;
  scope?: string;
  llm_config_id?: string;
}) =>
  j(`${BASE}/v1/judgment/prejudge`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

/** 查初判歸因任務進度 {status, total, processed, ok, failed, model, total_tokens, cost_usd}。 */
export const getPrejudgeStatus = (jobId: string) =>
  j(`${BASE}/v1/judgment/prejudge/status?job_id=${encodeURIComponent(jobId)}`);

/** 歸因聚合共用查詢參數（source 過濾 + 日期區間 + 趨勢粒度）。 */
export interface AttrQuery {
  /** 來源 code（省略＝全部來源） */
  source?: string;
  /** 起日 'YYYY-MM-DD'（含；省略＝不限） */
  dateFrom?: string;
  /** 迄日 'YYYY-MM-DD'（含；省略＝不限） */
  dateTo?: string;
  /** 趨勢粒度 year|month|day（省略＝後端預設 month；僅 overview 有效） */
  granularity?: string;
}

/**
 * 歸因縱覽聚合（縱覽頁專用）：KPI + 傾向/L1域/信心分層/星等 分布 + 趨勢。
 * 一次取齊，避免前端全量 fetch 29k 列再算。
 * @param opts 來源 / 日期區間 / 趨勢粒度（皆選填）
 */
export const getAttributionOverview = (opts: AttrQuery = {}) => {
  const q = new URLSearchParams();
  if (opts.source) q.set('source', opts.source);
  if (opts.dateFrom) q.set('date_from', opts.dateFrom);
  if (opts.dateTo) q.set('date_to', opts.dateTo);
  if (opts.granularity) q.set('granularity', opts.granularity);
  return j(`${BASE}/problems/attribution_overview?${q.toString()}`);
};

/**
 * 某 L1 歸因域下的 L2/L3 細項分布（縱覽長條點擊下鑽·懶載）。
 * @param l1 L1 歸因域 code（如 'supplier'）
 * @param opts 來源 / 日期區間（granularity 對下鑽無效，忽略）
 */
export const getAttributionBreakdown = (l1: string, opts: AttrQuery = {}) => {
  const q = new URLSearchParams({ l1 });
  if (opts.source) q.set('source', opts.source);
  if (opts.dateFrom) q.set('date_from', opts.dateFrom);
  if (opts.dateTo) q.set('date_to', opts.dateTo);
  return j(`${BASE}/problems/attribution_breakdown?${q.toString()}`);
};

/** 商品垂直分類解析結果：分組名 → 該組涵蓋的 CATEGORY 代碼清單（server-authoritative）。 */
export interface ProductVerticalResolved {
  groups: Record<string, string[]>;
}

/**
 * 取已解析的商品垂直分類（供篩選下拉；選項顯示分組名、送出亦送分組名，CATEGORY 代碼清單由後端展開）。
 * 資料源＝rule_code=product_vertical 的 active 版本（judge_rule_versions，可編輯版本化）；後端 product_vertical loader 解析。
 * @returns {groups:{分組名:[CATEGORY代碼,...]}}
 */
export const getProductVerticalResolved = (): Promise<ProductVerticalResolved> =>
  j(`${BASE}/judge-rules/product-vertical/resolved`);
