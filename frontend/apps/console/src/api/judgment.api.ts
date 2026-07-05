// 歸因領域 API：統一問題列表 + 即時匯總 + 初判歸因批量任務（選模型 + 進度輪詢）。
import { BASE, j } from './http.api';
import type { ProblemRow } from '@/features/judge/constants';

/** 統一問題列表查詢參數（source/judged/polarity 既有；scores/productVerticals/日期區間為新增篩選）。 */
export interface GetProblemsParams {
  source?: string;
  judged?: boolean;
  polarity?: string;
  /** 判決階段篩選（多選；unjudged/judged/pending_review/pending_data/insufficient；CSV 傳後端）。 */
  stage?: string[];
  /** 星等篩選（多選，IN 語意；僅有 score 欄的來源如 product_reviews 有效）。 */
  scores?: number[];
  /** 商品垂直分類名（多選；後端展開為 CATEGORY 代碼清單再篩，分組清單 server-authoritative）。 */
  productVerticals?: string[];
  /** 日期區間起（含，'YYYY-MM-DD'）。 */
  dateFrom?: string;
  /** 日期區間迄（含，'YYYY-MM-DD'）。 */
  dateTo?: string;
  /** 商品 prod_oid 精確過濾。 */
  prodOid?: string;
  /** 訂單 order_oid 精確過濾。 */
  orderOid?: string;
  /** 信心分層過濾（單選；auto_accept/jury/needs_review）。 */
  confidenceTier?: string;
  /** L1 歸因域過濾（單選；content/supplier/…，選項來自 getL1Domains）。 */
  l1Domain?: string;
  /** 排序欄（occurred_at/score/go_date/confidence；非白名單回退 occurred_at）。 */
  sortBy?: string;
  /** 排序方向（asc/desc；預設 desc）。 */
  sortDir?: 'asc' | 'desc';
  limit?: number;
  offset?: number;
}

/** 統一問題列表回應：每 review 一列（含 attributions 陣列）+ 符合篩選總數。 */
export interface ProblemListResp {
  rows: ProblemRow[];
  total: number;
}

/** 統一問題列表（intake + 歸因 join）。judged=true 僅已歸因。 */
export const getProblems = (params: GetProblemsParams = {}): Promise<ProblemListResp> => {
  const q = new URLSearchParams();
  if (params.source) q.set('source', params.source);
  if (params.judged !== undefined) q.set('judged', String(params.judged));
  if (params.polarity) q.set('polarity', params.polarity);
  if (params.stage?.length) q.set('stage', params.stage.join(','));
  if (params.scores?.length) q.set('scores', params.scores.join(','));
  if (params.productVerticals?.length) q.set('product_verticals', params.productVerticals.join(','));
  if (params.dateFrom) q.set('date_from', params.dateFrom);
  if (params.dateTo) q.set('date_to', params.dateTo);
  if (params.prodOid) q.set('prod_oid', params.prodOid);
  if (params.orderOid) q.set('order_oid', params.orderOid);
  if (params.confidenceTier) q.set('confidence_tier', params.confidenceTier);
  if (params.l1Domain) q.set('l1_domain', params.l1Domain);
  if (params.sortBy) q.set('sort_by', params.sortBy);
  if (params.sortDir) q.set('sort_dir', params.sortDir);
  q.set('limit', String(params.limit ?? 2000));
  q.set('offset', String(params.offset ?? 0));
  return j<ProblemListResp>(`${BASE}/problems?${q.toString()}`);
};

/** 某來源已判資料出現過的 L1 歸因域（供列表 L1 篩選下拉；code/label/count 皆來自 judgments.data distinct）。 */
export interface L1DomainOpt {
  code: string;
  label: string;
  count: number;
}

/** 取某來源 L1 歸因域清單（選項恆與可篩內容一致，見後端 db.list_l1_domains）。 */
export const getL1Domains = (source: string): Promise<L1DomainOpt[]> =>
  j<L1DomainOpt[]>(`${BASE}/problems/l1_domains?source=${encodeURIComponent(source)}`);

/**
 * 啟動問題列表導出背景 job（POST·item_ids 放 body 避免 URL 過長 431）→ {job_id, filename}（立即回）。
 * 進度走 /api/exports SSE（見 exports.api），完成後 downloadExport(job_id) 取檔；大列表可即時看進度並停止。
 */
export const startProblemsExport = (p: {
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
}): Promise<{ job_id: string; filename: string }> =>
  j<{ job_id: string; filename: string }>(`${BASE}/problems/export`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(p),
  });

/** 初判歸因批量任務啟動回應（job_id + 將處理總數 + 實際採用模型）。 */
export interface PrejudgeStartResp {
  job_id: string;
  total: number;
  model: string;
}

/** 啟動初判歸因批量任務（選擇驅動：item_ids 複選 / scope=all 全部未判 + 指定模型）→ {job_id, total, model}。 */
export const startPrejudge = (body: {
  item_ids?: string[];
  source?: string;
  scope?: string;
  llm_config_id?: string;
  product_verticals?: string[];
  /** 目標選取（scope=all；stage 驅動）：階段清單/傾向收斂/信心上限。 */
  stages?: string[];
  target_polarity?: string;
  max_confidence?: number;
}): Promise<PrejudgeStartResp> =>
  j<PrejudgeStartResp>(`${BASE}/v1/judgment/prejudge`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

/**
 * 初判歸因進度 SSE 串流 URL（供原生 EventSource 直接連；免輪詢）。
 * @param jobId startPrejudge 回傳的 job_id（capability token，端點免 auth header）
 */
export const prejudgeStreamUrl = (jobId: string): string =>
  `${BASE}/v1/judgment/prejudge/stream?job_id=${encodeURIComponent(jobId)}`;

/** 暫停初判歸因任務（提交迴圈阻塞，已在跑的收斂後 processed 停增）→ 更新後快照。 */
export const pausePrejudge = (jobId: string) =>
  j(`${BASE}/v1/judgment/prejudge/pause?job_id=${encodeURIComponent(jobId)}`, { method: 'POST' });

/** 恢復已暫停的初判歸因任務（提交迴圈續跑）→ 更新後快照。 */
export const resumePrejudge = (jobId: string) =>
  j(`${BASE}/v1/judgment/prejudge/resume?job_id=${encodeURIComponent(jobId)}`, { method: 'POST' });

/** 停止初判歸因任務（不再派新工，已在跑的收斂後轉 cancelled；已判已落庫，剩餘可重跑）→ 更新後快照。 */
export const cancelPrejudge = (jobId: string) =>
  j(`${BASE}/v1/judgment/prejudge/cancel?job_id=${encodeURIComponent(jobId)}`, { method: 'POST' });

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
  /** 全局商品垂直分類分組（多選；僅 product_reviews 生效） */
  productVerticals?: string[];
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
  if (opts.productVerticals?.length) q.set('product_verticals', opts.productVerticals.join(','));
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
  if (opts.productVerticals?.length) q.set('product_verticals', opts.productVerticals.join(','));
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
  j<ProductVerticalResolved>(`${BASE}/judge-rules/product-vertical/resolved`);
