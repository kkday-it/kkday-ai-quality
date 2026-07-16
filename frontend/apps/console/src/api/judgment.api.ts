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
  /** 覆核狀態過濾（多選 new/auto_confirmed/confirmed/dismissed；CSV 傳後端）。 */
  status?: string[];
  /** 判決模型過濾（多選；judgments.model IN——當前判決維度；CSV 傳後端）。 */
  model?: string[];
  /** 有無外部評論融合資料：'true'=有 / 'false'=無 / 缺省=全部（僅 product_reviews 生效）。 */
  hasExternal?: string;
  /** 歸因分類過濾（多選任意層級 code；後端 l1/l2_code 任一 IN 命中＝子樹語義）。 */
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
  if (params.status?.length) q.set('status', params.status.join(','));
  if (params.model?.length) q.set('model', params.model.join(','));
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
  /** 覆核狀態（多選 new/auto_confirmed/confirmed/dismissed）。 */
  status?: string[];
  /** 判決模型篩選（多選；當前判決維度，圈選哪些評論）。 */
  model?: string[];
  /** 輸出結果版本：省略＝當前判決；指定模型＝內容替換為該模型的 judgment_history 最新快照。 */
  snapshot_model?: string;
  /** 並排對比模型（可複選）：每模型在基準右側附一組欄「情緒·M/L1·M/L2·M」，值取該模型最新快照。 */
  compare_models?: string[];
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
  /** 版本選擇功能：7 條 prompt 各自指定歷史版本（{rule_code: 版本號}；未指定沿用 active）。 */
  prompt_versions?: Record<string, number>;
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

/** 六域裁決（B0 診斷理由 overlay）：命中域帶歸因+理由，棄權域帶棄權理由——六域皆有交代。 */
export interface DomainVerdict {
  domain: string;
  domain_label: string;
  matched: boolean;
  attributions: Array<{
    l1_domain_code: string;
    l1_label: string;
    l2_code: string;
    l2_label: string;
    confidence: number;
    evidence_quote: string;
    summary: Record<string, string>;
    reason: string;
  }>;
  abstain_reason: string;
}

/**
 * 初判歸因進度 SSE 串流 URL（供原生 EventSource 直接連；免輪詢）。
 * @param jobId startPrejudge 回傳的 job_id（capability token，端點免 auth header）
 */
export const prejudgeStreamUrl = (jobId: string): string =>
  `${BASE}/v1/judgment/prejudge/stream?job_id=${encodeURIComponent(jobId)}`;

/**
 * 初判執行日誌 SSE 串流 URL（抽屜即時檢視：各階段 + LLM 輸入參數/prompt/輸出；僅小批量 job 有日誌）。
 * @param jobId startPrejudge 回傳的 job_id（capability token，端點免 auth header）
 */
export const prejudgeLogStreamUrl = (jobId: string): string =>
  `${BASE}/v1/judgment/prejudge/log-stream?job_id=${encodeURIComponent(jobId)}`;

/** run_log 快照條目（供判決歷史回看當時的完整 LLM 日誌；形狀同 `PrejudgeLogDrawer` 的歷史日誌快照）。 */
export interface JudgmentRunLogEntry {
  ts: number;
  kind: 'stage' | 'llm_request' | 'llm_prompt' | 'llm_response' | 'llm_note' | 'error';
  stage: string;
  message: string;
  /** 同一次 LLM 調用的分組鍵（polarity / C-1..C-6）；供前端聚合成單一 tab。 */
  label?: string;
  data?: Record<string, unknown>;
}

/** 讀某次判決落庫的完整執行日誌快照（判決歷史「查看 LLM 日誌」入口）；
 * 僅小批量 job 有收集內容，無日誌時 404。 */
export const getJudgmentRunLog = (jobId: string): Promise<{ entries: JudgmentRunLogEntry[] }> =>
  j(`${BASE}/v1/judgment/runs/${encodeURIComponent(jobId)}/log`);

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
  /** 判決模型多選（judgments.model IN——當前判決維度；僅套判決級指標，total_intake 不受影響） */
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
  if (opts.productVerticals?.length) q.set('product_verticals', opts.productVerticals.join(','));
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
  if (opts.productVerticals?.length) q.set('product_verticals', opts.productVerticals.join(','));
  if (opts.model?.length) q.set('model', opts.model.join(','));
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
/** 批次判決中失敗的單筆（後端 snapshot.failed_items；error＝例外首行截斷）。 */
export interface PrejudgeFailedItem {
  item_id: string;
  source_id: string;
  error: string;
}

export interface JudgmentRun {
  job_id: string;
  /** 觸發型態：batch（目標選取）/ selected（顯式多筆）/ single（單筆）。 */
  kind: 'batch' | 'selected' | 'single';
  /** 標的先前已有判決 → 本次為重判。 */
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
  /** 失敗筆明細（後端上限 200）：供「重判本批失敗筆」收 item_id 與顯示失敗原因。 */
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

/** 歷來實際判決過的模型清單（judgments 當前 ∪ judgment_history 快照 distinct；stub 排最後）。 */
export const getJudgmentModels = (): Promise<string[]> =>
  j<string[]>(`${BASE}/judgment-history/models`);

/** Prompt 測試沙盒啟動請求 body：對 item_ids（或依條件解析出的目標集合）逐筆跑 prompt_ids 子集
 * （不受正式歸因閘門限制）。繼承 `PrejudgeBody` 全部目標選取欄位——item_ids 顯式優先；否則
 * scope="all" 依 stages 目標選取（可 within_ids 交集勾選範圍），與初判分類「依條件批量選取」
 * 同一套後端解析（`_resolve_target_ids`），零改動重用。
 * scope 由觸發入口顯式帶入（single＝單列按鈕；selection＝工具列對勾選多筆，item_ids 顯式；
 * all＝工具列「依條件批量」），不用筆數反推——供沙盒歷史列表分辨來源。 */
export interface PromptSandboxStartBody extends PrejudgeBody {
  source: string;
  prompt_ids: string[];
  scope: 'single' | 'selection' | 'all';
  /** 版本選擇功能：{rule_code: 指定歷史版本號}（沙盒獨有欄位，與 PrejudgeBody 繼承來的
   * prompt_versions 是不同的請求鍵，兩者互不影響）。不支援測試未存檔草稿。 */
  versions?: Record<string, number>;
}

/** 啟動 Prompt 測試沙盒背景 job → {job_id}（前端輪詢 `getPromptSandboxStatus` 拿進度）。
 * @throws stub 模式（無可用 LLM token）一律拒跑，dev 環境亦不例外——比照 `classify_one` 慣例。 */
export const startPromptSandbox = (body: PromptSandboxStartBody): Promise<{ job_id: string }> =>
  j<{ job_id: string }>(`${BASE}/v1/judgment/prompt-sandbox`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

/** 預覽 Prompt 測試沙盒「將測試 N 筆」（與 `startPromptSandbox` 同一套標的解析；不派工、不消耗 token）。 */
export const previewPromptSandboxCount = (body: PromptSandboxStartBody): Promise<{ total: number }> =>
  j<{ total: number }>(`${BASE}/v1/judgment/prompt-sandbox/count`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

/** Prompt 測試沙盒 job 進度快照（輪詢用）。 */
export interface PromptSandboxJobStatus {
  status: 'running' | 'done' | 'error';
  total: number;
  done: number;
  /** 完成後對應的歷史紀錄 run_id（供直接開啟詳情）；running/error 時為 null。 */
  run_id: string | null;
}

/** 沙盒測試 job 進度輪詢 → {status, total, done, run_id}。
 * @param jobId startPromptSandbox 回傳的 job_id。 */
export const getPromptSandboxStatus = (jobId: string): Promise<PromptSandboxJobStatus> =>
  j<PromptSandboxJobStatus>(
    `${BASE}/v1/judgment/prompt-sandbox/status?job_id=${encodeURIComponent(jobId)}`,
  );

/** 沙盒測試歷史列表單筆（不含 results/log，體積可觀，列表只列摘要）。 */
export interface PromptSandboxRunSummary {
  run_id: string;
  source: string;
  scope: 'single' | 'selection' | 'all';
  item_ids: string[];
  prompt_ids: string[];
  item_count: number;
  model: string;
  triggered_by: string;
  created_at: string;
  /** 本次測試各 prompt 指定的版本號（{rule_code: version}；未指定沿用 active）。 */
  versions?: Record<string, number>;
}

/** run_log 快照條目（供沙盒測試歷史回看完整 LLM log；形狀同 `PrejudgeLogDrawer` 的歷史日誌快照）。 */
export interface PromptSandboxLogEntry {
  ts: number;
  kind: 'stage' | 'llm_request' | 'llm_prompt' | 'llm_response' | 'llm_note' | 'error';
  stage: string;
  message: string;
  data?: Record<string, unknown>;
}

/** 單筆測試結果（`sandbox_classify` 輸出）：`prompts` 為異質清單——勾了 polarity 有一條
 * `{prompt_id:"polarity", matched, polarity, sentiment_score, reason}`；勾了域則各一條
 * `{prompt_id:"C-N", domain_label, matched, attributions, abstain_reason}`。 */
export interface PromptSandboxItemResult {
  source_id: string;
  text?: string;
  polarity?: string;
  sentiment_score?: number;
  prompts?: Array<{
    prompt_id: string;
    matched: boolean;
    /** polarity 條目專屬。 */
    polarity?: string;
    sentiment_score?: number;
    /** 域條目專屬。 */
    domain_label?: string;
    attributions?: DomainVerdict['attributions'];
    abstain_reason?: string;
    reason?: string;
  }>;
  /** 單筆判決失敗（如找不到評論）時的錯誤訊息，取代 prompts。 */
  error?: string;
}

/** 沙盒測試歷史列表（created_at 降冪分頁）→ {total, items}——與正式初判歷史完全分離。 */
export const listPromptSandboxRuns = (
  limit = 20,
  offset = 0,
): Promise<{ total: number; items: PromptSandboxRunSummary[] }> =>
  j<{ total: number; items: PromptSandboxRunSummary[] }>(
    `${BASE}/v1/judgment/prompt-sandbox/runs?limit=${limit}&offset=${offset}`,
  );

/** 單一沙盒測試 run 完整詳情：逐筆 results + 完整 LLM log 快照（供事後回看當時測試跑了什麼）。 */
export const getPromptSandboxRun = (
  runId: string,
): Promise<
  PromptSandboxRunSummary & {
    results: PromptSandboxItemResult[];
    log: PromptSandboxLogEntry[];
  }
> => j(`${BASE}/v1/judgment/prompt-sandbox/runs/${encodeURIComponent(runId)}`);
