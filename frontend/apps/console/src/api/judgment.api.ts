// 歸因領域 API：統一問題列表 + 即時匯總 + 初判歸因批量任務（選模型 + 進度輪詢）。
import { BASE, j } from './http.api';
import type { ProblemRow } from '@/features/judge/constants';

/** 統一問題列表查詢參數（傾向/階段/信心分層/歸因分類/垂直分類/日期區間/精確 id）。 */
export interface GetProblemsParams {
  source?: string;
  judged?: boolean;
  /** 傾向篩選（多選 positive/neutral/negative；CSV 傳後端）。 */
  polarity?: string[];
  /** 判決階段篩選（多選；unjudged/judged/pending_review/pending_data；CSV 傳後端）。 */
  stage?: string[];
  /** 商品垂直分類名（多選；後端展開為 CATEGORY 代碼清單再篩，分組清單 server-authoritative）。 */
  productVerticals?: string[];
  /** 日期區間起（含，'YYYY-MM-DD'）。 */
  dateFrom?: string;
  /** 日期區間迄（含，'YYYY-MM-DD'）。 */
  dateTo?: string;
  /** 評論 rec_oid 精確過濾（product_reviews 評論 id；對應各來源表 natural_key）。 */
  recOid?: string;
  /** 商品 prod_oid 精確過濾。 */
  prodOid?: string;
  /** 訂單 order_oid 精確過濾。 */
  orderOid?: string;
  /** 信心分層過濾（單選；auto_accept/jury/needs_review）。 */
  confidenceTier?: string;
  /** 有無外部評論融合資料：'true'=有 / 'false'=無 / 缺省=全部（僅 product_reviews 生效）。 */
  hasExternal?: string;
  /** 歸因分類過濾（多選任意層級 code；後端 l1/l2/l3_code 任一 IN 命中＝子樹語義）。 */
  taxonomy?: string[];
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
  if (params.polarity?.length) q.set('polarity', params.polarity.join(','));
  if (params.stage?.length) q.set('stage', params.stage.join(','));
  if (params.productVerticals?.length)
    q.set('product_verticals', params.productVerticals.join(','));
  if (params.dateFrom) q.set('date_from', params.dateFrom);
  if (params.dateTo) q.set('date_to', params.dateTo);
  if (params.recOid) q.set('rec_oid', params.recOid);
  if (params.prodOid) q.set('prod_oid', params.prodOid);
  if (params.orderOid) q.set('order_oid', params.orderOid);
  if (params.confidenceTier) q.set('confidence_tier', params.confidenceTier);
  if (params.taxonomy?.length) q.set('taxonomy', params.taxonomy.join(','));
  if (params.hasExternal) q.set('has_external', params.hasExternal);
  if (params.sortBy) q.set('sort_by', params.sortBy);
  if (params.sortDir) q.set('sort_dir', params.sortDir);
  q.set('limit', String(params.limit ?? 2000));
  q.set('offset', String(params.offset ?? 0));
  return j<ProblemListResp>(`${BASE}/problems?${q.toString()}`);
};

/**
 * 啟動問題列表導出背景 job（POST·item_ids 放 body 避免 URL 過長 431）→ {job_id, filename}（立即回）。
 * 進度走 /api/exports SSE（見 exports.api），完成後 downloadExport(job_id) 取檔；大列表可即時看進度並停止。
 */
export const startProblemsExport = (p: {
  source?: string;
  judged?: boolean;
  item_ids?: string[];
  /** 商品垂直分類名（多選；後端展開為 CATEGORY 代碼清單）。 */
  product_verticals?: string[];
  /** 日期區間起（含，'YYYY-MM-DD'）。 */
  date_from?: string;
  /** 日期區間迄（含，'YYYY-MM-DD'）。 */
  date_to?: string;
  /** 傾向（多選 positive/neutral/negative/unknown）。 */
  polarity?: string[];
  /** 判決階段（多選）。 */
  stage?: string[];
  /** 信心分層（單選）。 */
  confidence_tier?: string;
  /** 歸因分類（多選任意層級 code；子樹語義）。 */
  taxonomy?: string[];
  /** 有無外部評論（'true'/'false'）。 */
  has_external?: boolean;
  /** 精確 id 篩選。 */
  rec_oid?: string;
  prod_oid?: string;
  order_oid?: string;
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

/** 初判歸因批量任務請求 body（startPrejudge / previewPrejudgeCount 共用；預覽=實跑同一套標的解析）。 */
export interface PrejudgeBody {
  item_ids?: string[];
  source?: string;
  scope?: string;
  llm_config_id?: string;
  product_verticals?: string[];
  /** 目標選取（scope=all；stage 驅動）：階段清單/傾向收斂/信心上限。 */
  stages?: string[];
  target_polarity?: string[];
  max_confidence?: number;
  /** 範圍收斂（scope=all）：僅在此特徵 id 清單（勾選列）內做目標選取。 */
  within_ids?: string[];
  /** 列表全維度篩選（scope=all；語義同 /api/problems）：表級（兩分支皆套）。 */
  date_from?: string;
  date_to?: string;
  rec_oid?: string;
  prod_oid?: string;
  order_oid?: string;
  /** 判決級收斂（僅已判分支）：信心分層 / 歸因分類（多選任意層級 code，子樹語義）。 */
  confidence_tier?: string;
  taxonomy?: string[];
  /** 有無外部評論融合資料（表級，兩分支皆套；僅 product_reviews 生效）。 */
  has_external?: boolean;
}

/** 啟動初判歸因批量任務（item_ids 顯式 / scope=all 目標選取，可 within_ids 交集勾選範圍）→ {job_id, total, model}。 */
export const startPrejudge = (body: PrejudgeBody): Promise<PrejudgeStartResp> =>
  j<PrejudgeStartResp>(`${BASE}/v1/judgment/prejudge`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

/** 預覽初判歸因「將處理 N 筆」（與 startPrejudge 同一套標的解析；不派工、不消耗 token）。 */
export const previewPrejudgeCount = (body: PrejudgeBody): Promise<{ total: number }> =>
  j<{ total: number }>(`${BASE}/v1/judgment/prejudge/count`, {
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
 * 歸因概覽聚合（概覽頁專用）：KPI + 傾向/L1域/信心分層/星等 分布 + 趨勢。
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

/** 商品垂直分類解析結果：分組名 → 該組涵蓋的 CATEGORY 代碼清單（server-authoritative）。
 *  group_order＝分組顯示順序（顯式排序欄；jsonb 不保 key 序，舊版本內容可能缺欄）。 */
export interface ProductVerticalResolved {
  groups: Record<string, string[]>;
  group_order?: string[];
}

/**
 * 取已解析的商品垂直分類（供篩選下拉；選項顯示分組名、送出亦送分組名，CATEGORY 代碼清單由後端展開）。
 * 資料源＝rule_code=product_vertical 的 active 版本（judge_rule_versions，可編輯版本化）；後端 product_vertical loader 解析。
 * @returns {groups:{分組名:[CATEGORY代碼,...]}}
 */
export const getProductVerticalResolved = (): Promise<ProductVerticalResolved> =>
  j<ProductVerticalResolved>(`${BASE}/judge-rules/product-vertical/resolved`);

/** 歸因歷史單列（run 級：一次批量/選取/單筆重判＝一列；與 llm_usage 以 job_id 關聯）。 */
export interface JudgmentRun {
  job_id: string;
  /** 觸發型態：batch（目標選取）/ selected（顯式多筆）/ single（單筆）。 */
  kind: 'batch' | 'selected' | 'single';
  /** 標的先前已有判決 → 本次為重判。 */
  rejudge: boolean | null;
  source: string;
  model: string;
  ensemble_voters: number;
  /** 發起參數快照（stages / 商品垂直分類 / 傾向 / 信心上限 / item_ids 樣本…）。 */
  params: Record<string, unknown>;
  /** running/paused/cancelling（執行中 overlay 即時值）→ done/error/cancelled；interrupted＝server 重啟中斷。 */
  status: string;
  total: number;
  processed: number | null;
  ok: number | null;
  failed: number | null;
  total_tokens: number | null;
  cost_usd: number | null;
  triggered_by: string;
  started_at: string;
  finished_at: string | null;
}

/** 歸因歷史詳情的 per-stage LLM 用量明細（由 llm_usage 聚合；job 結束後才有值）。 */
export interface JudgmentRunStage {
  stage: string;
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  reasoning_tokens: number;
  cached_tokens: number;
  cost_usd: number;
}

/** 歸因歷史列表（started_at 降冪分頁；執行中列帶即時進度）→ {total, items}。 */
export const listJudgmentRuns = (p: { limit?: number; offset?: number; source?: string } = {}) => {
  const q = new URLSearchParams();
  if (p.limit != null) q.set('limit', String(p.limit));
  if (p.offset != null) q.set('offset', String(p.offset));
  if (p.source) q.set('source', p.source);
  return j<{ total: number; items: JudgmentRun[] }>(`${BASE}/v1/judgment/runs?${q.toString()}`);
};

/** 歸因歷史單筆詳情（run 欄位 + 參數快照 + per-stage LLM 用量明細）。 */
export const getJudgmentRun = (jobId: string) =>
  j<JudgmentRun & { stages: JudgmentRunStage[] }>(
    `${BASE}/v1/judgment/runs/${encodeURIComponent(jobId)}`,
  );
