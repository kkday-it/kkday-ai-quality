// 歸因領域 API：統一問題列表 + 即時匯總 + 初判歸因批量任務（選模型 + 進度輪詢）。
import { BASE, j } from './http.api';
import type { ProblemRow } from '@/features/judge/constants';
import type { LlmOverrides } from '@/features/settings/types';

/** 統一問題列表查詢參數（傾向/階段/信心分層/歸因分類/垂直分類/日期區間/精確 id）。 */
export interface GetProblemsParams {
  /** 人工介入狀態：ai_only / corrected / suggested（後端 list_problems 的 human_state）。 */
  humanState?: string;
  source?: string;
  judged?: boolean;
  /** 傾向篩選（多選 positive/neutral/negative；CSV 傳後端）。 */
  polarity?: string[];
  /** 初判階段篩選（多選；unjudged/judged/pending_review/pending_data；CSV 傳後端）。 */
  stage?: string[];
  /** 商品垂直分類名（多選；後端展開為 bd_tag 代碼清單再篩，清單 server-authoritative）。 */
  verticals?: string[];
  /** 日期區間起（含，'YYYY-MM-DD'）。 */
  dateFrom?: string;
  /** 日期區間迄（含，'YYYY-MM-DD'）。 */
  dateTo?: string;
  /** 評論 rec_oid 精確過濾（reviews 評論 id；對應各來源表 natural_key）。 */
  recOid?: string;
  /** 商品 prod_oid 精確過濾。 */
  prodOid?: string;
  /** 訂單 order_oid 精確過濾。 */
  orderOid?: string;
  /** 信心分層過濾（單選；auto_accept/jury/needs_review）。 */
  confidenceTier?: string;
  /** 初判模型過濾（多選；attributions.model IN——當前初判維度；CSV 傳後端）。 */
  model?: string[];
  /** 有無外部評論融合資料：'true'=有 / 'false'=無 / 缺省=全部（僅 reviews 生效）。 */
  hasExternal?: string;
  /** 歸因分類過濾（多選任意層級 code；後端 l1/l2_code 任一 IN 命中＝子樹語義）。 */
  taxonomy?: string[];
  /** 進線分桶過濾（多選；conversations 專屬直欄，其餘來源忽略）。 */
  bucket?: string[];
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
  if (params.verticals?.length) q.set('verticals', params.verticals.join(','));
  if (params.dateFrom) q.set('date_from', params.dateFrom);
  if (params.dateTo) q.set('date_to', params.dateTo);
  if (params.recOid) q.set('rec_oid', params.recOid);
  if (params.prodOid) q.set('prod_oid', params.prodOid);
  if (params.orderOid) q.set('order_oid', params.orderOid);
  if (params.confidenceTier) q.set('confidence_tier', params.confidenceTier);
  if (params.model?.length) q.set('model', params.model.join(','));
  if (params.taxonomy?.length) q.set('taxonomy', params.taxonomy.join(','));
  if (params.bucket?.length) q.set('bucket', params.bucket.join(','));
  if (params.humanState) q.set('human_state', params.humanState);
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
  /** 商品垂直分類名（多選；後端展開為 bd_tag 代碼清單）。 */
  verticals?: string[];
  /** 日期區間起（含，'YYYY-MM-DD'）。 */
  date_from?: string;
  /** 日期區間迄（含，'YYYY-MM-DD'）。 */
  date_to?: string;
  /** 傾向（多選 positive/neutral/negative/unknown）。 */
  polarity?: string[];
  /** 初判階段（多選）。 */
  stage?: string[];
  /** 信心分層（單選）。 */
  confidence_tier?: string;
  /** 歸因分類（多選任意層級 code；子樹語義）。 */
  taxonomy?: string[];
  /** 初判模型篩選（多選；當前初判維度，圈選哪些評論）。 */
  model?: string[];
  /** 輸出結果版本：省略＝當前初判；指定模型＝內容替換為該模型的 attribution_history 最新快照。 */
  snapshot_model?: string;
  /** 並排對比模型（可複選）：每模型在基準右側附一組欄「情緒·M/L1·M/L2·M」，值取該模型最新快照。 */
  compare_models?: string[];
  /** 有無外部評論（'true'/'false'）。 */
  has_external?: boolean;
  /** 精確 id 篩選。 */
  rec_oid?: string;
  prod_oid?: string;
  order_oid?: string;
  /** 進線分桶（conversations 專屬直欄；其餘來源忽略，與列表篩選對齊）。 */
  bucket?: string[];
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
  /** 本次執行 LLM 覆寫（provider+旋鈕）；缺省沿用 prejudge 功能區默認。 */
  overrides?: LlmOverrides;
  verticals?: string[];
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
  /** 初判級收斂（僅已初判分支）：信心分層 / 歸因分類（多選任意層級 code，子樹語義）。 */
  confidence_tier?: string;
  taxonomy?: string[];
  /** 有無外部評論融合資料（表級，兩分支皆套；僅 reviews 生效）。 */
  has_external?: boolean;
  /** 版本選擇功能：7 條 prompt 各自指定歷史版本（{rule_code: 版本號}；未指定沿用 active）。 */
  prompt_versions?: Record<string, number>;
}

/** 啟動初判歸因批量任務（item_ids 顯式 / scope=all 目標選取，可 within_ids 交集勾選範圍）→ {job_id, total, model}。 */
export const startPrejudge = (body: PrejudgeBody): Promise<PrejudgeStartResp> =>
  j<PrejudgeStartResp>(`${BASE}/v1/prejudge`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

/** 預覽初判歸因「將處理 N 筆」（與 startPrejudge 同一套標的解析；不派工、不消耗 token）。 */
export const previewPrejudgeCount = (body: PrejudgeBody): Promise<{ total: number }> =>
  j<{ total: number }>(`${BASE}/v1/prejudge/count`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

/**
 * 初判歸因進度 SSE 串流 URL（供原生 EventSource 直接連；免輪詢）。
 * @param jobId startPrejudge 回傳的 job_id（capability token，端點免 auth header）
 */
export const prejudgeStreamUrl = (jobId: string): string =>
  `${BASE}/v1/prejudge/stream?job_id=${encodeURIComponent(jobId)}`;

/**
 * 初判執行日誌 SSE 串流 URL（抽屜即時檢視：各階段 + LLM 輸入參數/prompt/輸出；僅小批量 job 有日誌）。
 * @param jobId startPrejudge 回傳的 job_id（capability token，端點免 auth header）
 */
export const prejudgeLogStreamUrl = (jobId: string): string =>
  `${BASE}/v1/prejudge/log-stream?job_id=${encodeURIComponent(jobId)}`;

/** run_log 快照條目（供歸因歷史回看當時的完整 LLM 日誌；形狀同 `PrejudgeLogDrawer` 的歷史日誌快照）。 */
export interface PrejudgeRunLogEntry {
  ts: number;
  kind: 'stage' | 'llm_request' | 'llm_prompt' | 'llm_response' | 'llm_note' | 'error';
  stage: string;
  message: string;
  /** 同一次 LLM 調用的分組鍵（polarity / C-1..C-6）；供前端聚合成單一 tab。 */
  label?: string;
  data?: Record<string, unknown>;
}

/** 某次初判落庫的執行日誌（一則評論一列，故可只取單則）。 */
export interface PrejudgeRunLog {
  entries: PrejudgeRunLogEntry[];
  /** 本 job 有日誌的評論清單（不含 job 級事件列）；整批視角下供逐則點選。 */
  items: { source_id: string; count: number }[];
  /** 整批視角是否有評論未併入 entries（大批量只合併前 N 則）。 */
  truncated: boolean;
}

/** 讀某次初判落庫的執行日誌（歸因歷史「查看 LLM 日誌」入口）。
 * 不分批量大小皆有日誌（後端逐筆落庫）；`sourceId` 指定只取該則評論，省去整批傳輸。
 * 僅「啟用逐筆落庫前、當初就沒收日誌」的舊大批量 job 會 404。 */
export const getPrejudgeRunLog = (jobId: string, sourceId?: string): Promise<PrejudgeRunLog> =>
  j(
    `${BASE}/v1/prejudge/runs/${encodeURIComponent(jobId)}/log` +
      (sourceId ? `?source_id=${encodeURIComponent(sourceId)}` : ''),
  );

/** 暫停初判歸因任務（提交迴圈阻塞，已在跑的收斂後 processed 停增）→ 更新後快照。 */
export const pausePrejudge = (jobId: string) =>
  j(`${BASE}/v1/prejudge/pause?job_id=${encodeURIComponent(jobId)}`, { method: 'POST' });

/** 恢復已暫停的初判歸因任務（提交迴圈續跑）→ 更新後快照。 */
export const resumePrejudge = (jobId: string) =>
  j(`${BASE}/v1/prejudge/resume?job_id=${encodeURIComponent(jobId)}`, { method: 'POST' });

/** 停止初判歸因任務（不再派新工，已在跑的收斂後轉 cancelled；已初判已落庫，剩餘可重跑）→ 更新後快照。 */
export const cancelPrejudge = (jobId: string) =>
  j(`${BASE}/v1/prejudge/cancel?job_id=${encodeURIComponent(jobId)}`, { method: 'POST' });

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
  /** 全局商品垂直分類（多選；bd_tag_col 存在的來源生效） */
  verticals?: string[];
  /** 初判模型多選（attributions.model IN——當前初判維度；僅套初判級指標，total_intake 不受影響） */
  model?: string[];
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
  if (opts.verticals?.length) q.set('verticals', opts.verticals.join(','));
  if (opts.model?.length) q.set('model', opts.model.join(','));
  return j(`${BASE}/problems/attribution_overview?${q.toString()}`);
};

/**
 * 某 L1 歸因域下的 L2 面向分布（縱覽長條點擊下鑽·懶載）。
 * @param l1 L1 歸因域 code（如 'supplier'）
 * @param opts 來源 / 日期區間（granularity 對下鑽無效，忽略）
 */
export const getAttributionBreakdown = (l1: string, opts: AttrQuery = {}) => {
  const q = new URLSearchParams({ l1 });
  if (opts.source) q.set('source', opts.source);
  if (opts.dateFrom) q.set('date_from', opts.dateFrom);
  if (opts.dateTo) q.set('date_to', opts.dateTo);
  if (opts.verticals?.length) q.set('verticals', opts.verticals.join(','));
  if (opts.model?.length) q.set('model', opts.model.join(','));
  return j(`${BASE}/problems/attribution_breakdown?${q.toString()}`);
};

/** 商品垂直分類解析結果：verticals＝去重排序後的 Vertical 名稱清單（篩選下拉選項，server-authoritative）；
 *  items＝bd_tag 代碼 → {note,pm,vertical} 對照（設定頁表格編輯器用）。 */
export interface VerticalResolved {
  verticals: string[];
  items: Record<string, { note?: string; pm: string; vertical: string }>;
}

/**
 * 取已解析的商品垂直分類（供篩選下拉；選項顯示/送出皆為 Vertical 名稱，bd_tag 代碼由後端展開）。
 * 資料源＝rule_code=bd_tag_vertical 的 active 版本（judge_rule_versions，可編輯版本化）；後端
 * bd_tag_vertical loader 解析。取代舊制 getProductVerticalResolved（CATEGORY_xxx 分組）。
 * @returns {verticals:[名稱,...], items:{代碼:{note,pm,vertical}}}
 */
export const getVerticalResolved = (): Promise<VerticalResolved> =>
  j<VerticalResolved>(`${BASE}/judge-rules/bd-tag-vertical/resolved`);

/** 歸因歷史單列（run 級：一次批量/選取/單筆重新初判＝一列；與 llm_usage 以 job_id 關聯）。 */
/** 批次初判中失敗的單筆（後端 snapshot.failed_items；error＝例外首行截斷）。 */
export interface PrejudgeFailedItem {
  item_id: string;
  source_id: string;
  error: string;
}

export interface PrejudgeRun {
  job_id: string;
  /** 觸發型態：batch（目標選取）/ selected（顯式多筆）/ single（單筆）。 */
  kind: 'batch' | 'selected' | 'single';
  /** 標的先前已有初判 → 本次為重新初判。 */
  rejudge: boolean | null;
  source: string;
  model: string;
  /** 發起參數快照（stages / 商品垂直分類 / 傾向 / 信心上限 / item_ids 樣本…）。 */
  params: Record<string, unknown>;
  /** running/paused/cancelling（執行中 overlay 即時值）→ done/error/cancelled；interrupted＝server 重啟中斷。 */
  status: string;
  total: number;
  processed: number | null;
  ok: number | null;
  failed: number | null;
  /** 失敗筆明細（後端上限 200）：供「重新初判本批失敗筆」收 item_id 與顯示失敗原因。 */
  failed_items?: PrejudgeFailedItem[];
  /** 失敗筆超過後端上限、清單已截斷（只計數、不再細列）。 */
  failed_items_truncated?: boolean;
  total_tokens: number | null;
  cost_usd: number | null;
  triggered_by: string;
  started_at: string;
  finished_at: string | null;
}

/** 歸因歷史詳情的 per-stage LLM 用量明細（由 llm_usage 聚合；job 結束後才有值）。 */
export interface PrejudgeRunStage {
  stage: string;
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  reasoning_tokens: number;
  cached_tokens: number;
  cost_usd: number;
}

/** 歸因歷史列表（started_at 降冪分頁；執行中列帶即時進度）→ {total, items}。 */
export const listPrejudgeRuns = (p: { limit?: number; offset?: number; source?: string } = {}) => {
  const q = new URLSearchParams();
  if (p.limit != null) q.set('limit', String(p.limit));
  if (p.offset != null) q.set('offset', String(p.offset));
  if (p.source) q.set('source', p.source);
  return j<{ total: number; items: PrejudgeRun[] }>(`${BASE}/v1/prejudge/runs?${q.toString()}`);
};

/** 歸因歷史單筆詳情（run 欄位 + 參數快照 + per-stage LLM 用量明細）。 */
export const getPrejudgeRun = (jobId: string) =>
  j<PrejudgeRun & { stages: PrejudgeRunStage[] }>(
    `${BASE}/v1/prejudge/runs/${encodeURIComponent(jobId)}`,
  );

/** 歷來實際初判過的模型清單（attributions 當前 ∪ attribution_history 快照 distinct；stub 排最後）。 */
export const getPrejudgeModels = (): Promise<string[]> =>
  j<string[]>(`${BASE}/attribution-history/models`);
