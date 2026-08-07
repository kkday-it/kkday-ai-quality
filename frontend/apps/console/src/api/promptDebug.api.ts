// 售後根因 Prompt 調試台：預設資料 + POST SSE 串流 client + 人工評判案例庫 + AI 定點改寫。
import { BASE, getToken, j, JSON_HEADERS, postSse, type SseFrame } from './http.api';
import type { LlmOverrides } from '@/features/settings/types';

/** 一份草稿的 meta（不含全文；全文走 `getPromptDraft`）。 */
export interface PromptDraftMeta {
  /** 草稿名＝時間戳檔名（YYYY-MM-DD-HHMMSS）。 */
  version: string;
  note: string;
  author: string;
  saved_at: string;
}

/** 一份正式版的 meta（不含全文；全文走 `getPromptRelease`）。 */
export interface PromptReleaseMeta {
  /** 正式版名＝人取的自訂名（如 release-v1）。 */
  name: string;
  /** 升版來源的草稿名。 */
  source_draft: string;
  note: string;
  author: string;
  promoted_at: string;
  /** 是否為當前線上口徑（恰有一個為 true）。 */
  is_active: boolean;
}

export interface PromptDebugDefaults {
  /** 當前線上口徑的正式版名；**可能為空字串**（尚未升過任何版，草稿工作流仍正常）。 */
  active_release: string;
  /** 正式版清單（含 meta 與 is_active 標記）。 */
  releases: PromptReleaseMeta[];
  /** 草稿清單（新→舊）。 */
  drafts: PromptDraftMeta[];
  /** 最新草稿名（`drafts[0].version`）；無草稿時為空字串。 */
  latest_draft: string;
  /**
   * 頁面載入口徑的 system prompt 全文＝**最新草稿**（調試台是草稿工作台）。
   * 無草稿時才退回正式版全文；兩者都無＝空字串。
   */
  system_prompt: string;
  /** 當前正式版全文，供口徑開關撥到「正式」側時即時切換；無正式版＝空字串。 */
  release_prompt: string;
  output_schema: Record<string, unknown>;
  /**
   * 受控欄的上下層級聯（L1 → L2 → L3）：
   * 下層欄位鍵 → `{ parent: 上層欄位鍵, options_by_parent: 各上層值底下的可選清單 }`。
   * schema 的 enum 是攤平的全域值域，填正解時要靠這份把選單限縮到已選上層底下。
   */
  output_cascade: Record<string, { parent: string; options_by_parent: Record<string, string[]> }>;
  output_fields: Array<{
    key: string;
    label: string;
    hint: string;
  }>;
  taxonomy_version: string;
  L2_count: number;
  L1_count: number;
  analyzed_rows: number;
  oot_rows: number;
  oot_rate: number;
  mean_confidence: number;
  sources: {
    knowledge_document: { title: string; url: string; revision_id?: string };
    field_definitions_document: { title: string; url: string; revision_id?: string };
    judge_spreadsheet: { title: string; url: string; sheet_name: string };
  };
}

export interface PromptDebugBody {
  text: string;
  /** 留空＝用版本庫最新版；頁面上臨時編輯過才送全文。 */
  system_prompt: string;
  /** 本次執行 LLM 覆寫（provider+旋鈕）；缺省沿用 prompt_debug 功能區默認。 */
  overrides?: LlmOverrides;
}

export interface PromptDebugMeta {
  job_id: string;
  model: string;
  provider: string;
  base_url: string;
  temperature: number | null;
  thinking: string;
  reasoning_effort: string;
}

export interface PromptDebugResult {
  raw: string;
  parsed: Record<string, unknown> | null;
  valid: boolean;
  validation_issues: string[];
}

export interface PromptDebugUsage {
  model: string;
  prompt_tokens: number;
  cached_tokens: number;
  completion_tokens: number;
  reasoning_tokens: number;
  total_tokens: number;
  cost_usd: number;
  latency_ms: number;
  usage_available: boolean;
  estimated: boolean;
}

export interface PromptDebugHandlers {
  onMeta?: (payload: PromptDebugMeta) => void;
  onDelta?: (text: string) => void;
  onWarning?: (message: string) => void;
  onResult?: (payload: PromptDebugResult) => void;
  onUsage?: (payload: PromptDebugUsage) => void;
  onError?: (message: string) => void;
  onDone?: () => void;
}

export const getPromptDebugDefaults = (): Promise<PromptDebugDefaults> =>
  j<PromptDebugDefaults>(`${BASE}/v1/prejudge/prompt-debug/defaults`);

/** 存為新版本的結果；created=false＝內容與最新版逐字相同，未建檔。 */
export interface PromptVersionSaved {
  version: string;
  created: boolean;
  drafts: PromptDraftMeta[];
}

/** 升版結果：新的 active 正式版名、來源草稿、升版前的 active。 */
export interface PromptReleasePromoted {
  name: string;
  source_draft: string;
  previous_active: string;
  releases: PromptReleaseMeta[];
}

/**
 * 把編輯後的 Prompt 存成新草稿。**不改變線上口徑**——要上線得再走 `promotePromptRelease`。
 * @param systemPrompt 要存檔的 system prompt 全文
 * @param note 備註（可空）
 */
export const savePromptDraft = (systemPrompt: string, note = ''): Promise<PromptVersionSaved> =>
  j<PromptVersionSaved>(`${BASE}/v1/prejudge/prompt-debug/drafts`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ system_prompt: systemPrompt, note }),
  });

/** 取單一草稿全文（版本對比用）。 */
export const getPromptDraft = (
  version: string,
): Promise<{ version: string; system_prompt: string }> =>
  j(`${BASE}/v1/prejudge/prompt-debug/drafts/${encodeURIComponent(version)}`);

/** 取單一正式版全文（版本對比用）。 */
export const getPromptRelease = (name: string): Promise<{ name: string; system_prompt: string }> =>
  j(`${BASE}/v1/prejudge/prompt-debug/releases/${encodeURIComponent(name)}`);

/**
 * 依「反饋來源 + 單一自然鍵」撈該筆對話原文，供調試台把 DB 內容直接填進調試文本框。
 * 與跑批 DB 取數同一條解析路徑（來源註冊表 + canonical 映射），差別在單筆即時、不落快照。
 * @param source 反饋來源 id（如 `conversations`）
 * @param itemId 該來源的自然鍵值（如 session_oid）
 * @throws {Error} 404——查無此列或該筆對話內容為空
 */
export const getSourceItemText = (
  source: string,
  itemId: string,
): Promise<{ source: string; item_id: string; content: string }> =>
  j(
    `${BASE}/v1/prejudge/prompt-debug/source-item?source=${encodeURIComponent(source)}&item_id=${encodeURIComponent(itemId)}`,
  );

/**
 * 把某個**已存檔的草稿**升為正式版，立即成為線上唯一口徑（跑批與調試台預設都改用它）。
 * 需 `judge-rule.version.manage`。
 * @param draft 來源草稿名（時間戳）
 * @param name 正式版名稱（英數與 . _ -，首字元須為英數）
 * @param note 上線理由（建議必填，供日後回顧）
 */
export const promotePromptRelease = (
  draft: string,
  name: string,
  note = '',
): Promise<PromptReleasePromoted> =>
  j<PromptReleasePromoted>(`${BASE}/v1/prejudge/prompt-debug/releases`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ draft, name, note }),
  });

/**
 * 把線上口徑切到某個**既有**正式版（回退／切換上線版本）。需 `judge-rule.version.manage`。
 *
 * 與 `promotePromptRelease` 的分工：升版是「草稿 → 新正式版」（複製檔案 + 新增版本紀錄），
 * 本函式只改 active 指標。升錯版時沒有這條路就只能再升一版，版本號無謂膨脹。
 * @param name 目標正式版名（必須已存在）
 */
export const activatePromptRelease = (
  name: string,
): Promise<{ name: string; previous_active: string; releases: PromptReleaseMeta[] }> =>
  j(`${BASE}/v1/prejudge/prompt-debug/releases/${encodeURIComponent(name)}/activate`, {
    method: 'POST',
    headers: JSON_HEADERS,
  });

// ── 人工評判案例（**存於前端本地**，見 stores/promptReviewCases.store）──────────
//
// 2026-08-04 起後端不再有 `prompt_debug_reviews` 表與其 CRUD 端點：案例是個人調試用的暫存語料，
// 不是團隊共享資產。改寫／回歸端點改為由請求**整包帶上案例內容**，後端純運算不持久化。

/** 送給改寫／回歸端點的單則案例（形狀對齊後端 `PromptDebugCaseIn`）。 */
export interface PromptDebugCasePayload {
  /** 本地案例 id（僅供進度回報對應回列表）。 */
  id: number;
  conversation: string;
  ai_output: Record<string, unknown>;
  /** 人標的正解 `{欄名: 正解值}`；只含被標錯的欄，`{}`＝全欄皆對（正例）。 */
  corrections: Record<string, unknown>;
  /** 人明確標「對」的欄名；不得與 corrections 重疊（後端 validator 會擋）。 */
  confirmed: string[];
  comment?: string;
}

// ── AI 定點改寫（/prompt-debug/revise[/apply]）─────────────────────────────

/** anchor 命中狀態：只有 matched 能套用；另兩種仍顯示（「模型想改哪裡」本身有診斷價值）。 */
export type PromptPatchStatus = 'matched' | 'not_found' | 'ambiguous';

/** 一條定點補丁。 */
export interface PromptPatch {
  /** 要被取代的原文片段（模型逐字複製自現行 Prompt）。 */
  anchor: string;
  replacement: string;
  reason: string;
  /** 這樣改後可能被錯誤吸過來的案例類型。 */
  risk: string;
  status: PromptPatchStatus;
  /** anchor 在全文中的出現次數（0＝沒逐字複製、>1＝片段太短撞多處）。 */
  occurrences: number;
}

/** 改寫結果幀。 */
export interface PromptReviseResult {
  raw: string;
  /** 洞在哪：這批案例暴露出哪條判準綁錯了軸。 */
  diagnosis: string;
  /** CHANGELOG 條目草稿（markdown）。 */
  changelog: string;
  patches: PromptPatch[];
  /** 可套用（status=matched）的條數。 */
  applicable: number;
}

/** 改寫的 meta 幀。 */
export interface PromptReviseMeta {
  job_id: string;
  model: string;
  provider: string;
  reasoning_effort: string;
  case_count: number;
  prompt_chars: number;
}

export interface PromptReviseHandlers {
  onMeta?: (payload: PromptReviseMeta) => void;
  onDelta?: (text: string) => void;
  /** 相容端點參數降級提示（與調試台同一套文案，來自後端共用降級階梯）。 */
  onWarning?: (message: string) => void;
  onResult?: (payload: PromptReviseResult) => void;
  onUsage?: (payload: PromptDebugUsage) => void;
  onError?: (message: string) => void;
  onDone?: () => void;
}

/** 依選中案例串流產出定點補丁；`systemPrompt` 留空＝用版本庫最新版。 */
export const streamPromptRevise = (
  body: { cases: PromptDebugCasePayload[]; system_prompt: string; overrides?: LlmOverrides },
  handlers: PromptReviseHandlers,
  signal?: AbortSignal,
): Promise<void> =>
  postSse(
    `${BASE}/v1/prejudge/prompt-debug/revise`,
    body,
    ({ event, payload }) => {
      if (payload === null) return handlers.onError?.('收到無法解析的 SSE 事件');
      if (event === 'meta') handlers.onMeta?.(payload as unknown as PromptReviseMeta);
      else if (event === 'delta') handlers.onDelta?.(String(payload.text ?? ''));
      else if (event === 'warning') handlers.onWarning?.(String(payload.message ?? ''));
      else if (event === 'result') handlers.onResult?.(payload as unknown as PromptReviseResult);
      else if (event === 'usage') handlers.onUsage?.(payload as unknown as PromptDebugUsage);
      else if (event === 'error') handlers.onError?.(String(payload.message ?? '未知錯誤'));
      else if (event === 'done') handlers.onDone?.();
    },
    signal,
  );

/**
 * 把勾選的補丁套進全文，回套用後的內容（不落檔；要成為線上口徑仍須「存為新版本」）。
 * @throws {Error} 任一 anchor 對不上或撞多處（後端 400，訊息帶片段）。
 */
export const applyPromptPatches = (
  systemPrompt: string,
  patches: Array<{ anchor: string; replacement: string }>,
): Promise<{ system_prompt: string; chars_before: number; chars_after: number }> =>
  j(`${BASE}/v1/prejudge/prompt-debug/revise/apply`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ system_prompt: systemPrompt, patches }),
  });

// ── 回歸重跑（/prompt-debug/regression）─────────────────────────────────────

/** 單一欄位的回歸判定明細；`held` 只帶 field（沒變就沒什麼好比的）。 */
export interface RegressionFieldDelta {
  field: string;
  expected?: unknown;
  actual?: unknown;
}

/** 單一案例的回歸結果。 */
export interface RegressionCaseResult {
  review_id: number;
  /** 對話開頭 80 字（認出是哪一則用）。 */
  preview: string;
  ok: boolean;
  error: string;
  /** 人標錯的欄，這次判對了。 */
  fixed: RegressionFieldDelta[];
  /** 人標錯的欄，這次還是不對。 */
  still_wrong: RegressionFieldDelta[];
  /** 人標對的欄，這次被改壞了。 */
  broken: RegressionFieldDelta[];
  /** 人標對的欄，這次守住了。 */
  held: RegressionFieldDelta[];
  total_tokens: number;
  cost_usd: number;
}

/** 回歸 job 進度快照（輪詢 GET /regression/{job_id}）。 */
export interface PromptRegressionSnapshot {
  job_id: string;
  status: 'running' | 'done' | 'error';
  total: number;
  processed: number;
  model: string;
  prompt_chars: number;
  cases: RegressionCaseResult[];
  /** 以下四項為欄位級累計。 */
  fixed: number;
  still_wrong: number;
  broken: number;
  held: number;
  /** 重跑失敗的案例數。 */
  failed: number;
  cost_usd: number;
  total_tokens: number;
  error: string;
}

/** 啟動回歸重跑；`systemPrompt` 留空＝用版本庫最新版。回初始快照（含 job_id）。 */
export const startPromptRegression = (body: {
  cases: PromptDebugCasePayload[];
  system_prompt: string;
  overrides?: LlmOverrides;
}): Promise<PromptRegressionSnapshot> =>
  j<PromptRegressionSnapshot>(`${BASE}/v1/prejudge/prompt-debug/regression`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(body),
  });

/** 回歸進度輪詢。 */
export const getPromptRegression = (jobId: string): Promise<PromptRegressionSnapshot> =>
  j<PromptRegressionSnapshot>(
    `${BASE}/v1/prejudge/prompt-debug/regression/${encodeURIComponent(jobId)}`,
  );

/** SSE 串流單條裁決（串流讀取走共用 `postSse`，與 AI 改寫同一套幀解析）。 */
export const streamPromptDebug = (
  body: PromptDebugBody,
  handlers: PromptDebugHandlers,
  signal?: AbortSignal,
): Promise<void> =>
  postSse(
    `${BASE}/v1/prejudge/prompt-debug/stream`,
    body,
    (frame) => dispatchFrame(frame, handlers),
    signal,
  );

// ── 批量跑批（/prompt-debug/batch/*）：上傳 CSV/XLSX 以當前 Prompt/契約整批裁決，斷點續跑 ──

/** 跑批 run 狀態機：running → done｜cancelling → cancelled｜error；interrupted＝server 重啟遺留（可續跑）。 */
export type PromptDebugBatchStatus =
  'running' | 'cancelling' | 'done' | 'error' | 'cancelled' | 'interrupted';

/** 快照內「最近完成」明細（即時回報環，全量成功明細在結果 CSV）。 */
export interface PromptDebugBatchRecentItem {
  item_id: string;
  /** 這一筆是否成功（與快照的 `ok_count` 計數刻意不同名——同名不同義是這個模組出過的事故）。 */
  succeeded: boolean;
  L1: string | null;
  L2: string | null;
  /** 欄位校驗未過項數（0＝契約通過）。 */
  issues: number;
  latency_ms: number | null;
  error: string | null;
}

/** 跑批 run 進度快照（輪詢 GET /batch/runs/{run_id}；對齊後端 prompt_debug_batch._new_snapshot）。 */
export interface PromptDebugBatchSnapshot {
  status: PromptDebugBatchStatus;
  run_id: string;
  /** 本批用的 Prompt 版本名；空＝送出前在頁面上臨時編輯過（實際內容以 run 目錄快照為準）。 */
  prompt_version: string;
  /** 版本口徑軌跡（release / draft / 臨時編輯）——稽核「那批到底用哪份 Prompt 跑的」靠它。 */
  prompt_kind: string;
  model: string;
  input_name: string;
  created_at: string;
  /** 本次選中目標（limit 後）。**`null`＝未知**：server 重啟後由磁碟推導的 run
   * 拿不到當時的選中總數，此時不得拿來算進度百分比（過去後端回 0，前端就顯示「目標 0 / 成功 42」）。 */
  total: number | null;
  /** 斷點復用的成功筆。 */
  resumed: number;
  /** 本次實際要請求的筆數。 */
  pending: number;
  /** 本次已完成請求數（成功+失敗）。 */
  processed: number;
  /** 累計成功**筆數**（含斷點復用）。 */
  ok_count: number;
  failed: number;
  /** 成功但欄位校驗未過（詳情落在 run 目錄的 raw_results.jsonl `validation_issues`，不對外下載）。 */
  invalid: number;
  total_tokens: number;
  cost_usd: number;
  /** 本次啟動時間（ISO 8601 UTC）；`null`＝磁碟推導的 run（重啟後 in-mem 已失）。
   * ⚠️ 2026-07-31 前這裡是 epoch 秒（float），與同物件的 `created_at`/`finished_at` 型別不一致；
   * 已統一為 ISO，前端不必再為「這個時間欄是哪種格式」分支。 */
  started_at: string | null;
  /** 本次收尾時間（ISO 8601 UTC）；空字串＝尚未收尾。 */
  finished_at: string;
  /** 本次執行段落已跑的秒數（執行中＝算到現在）；`null`＝未知（改造前的舊 run／中斷推導）。 */
  elapsed_sec: number | null;
  /** 含歷次續跑的**累計**執行秒數。刻意不是 `created_at → finished_at` 的牆鐘——
   * 中斷後隔天才續跑的話，牆鐘會把擱置的那一整晚也算成「跑批花的時間」。 */
  elapsed_total_sec: number | null;
  /** 觸發人 email（`_launch` 額外塞進快照的欄位）。 */
  triggered_by: string;
  warnings: string[];
  recent: PromptDebugBatchRecentItem[];
  failed_items: Array<{ item_id: string; error: string }>;
  failed_items_truncated: boolean;
  /** status=error 時的整批級錯誤訊息。 */
  error?: string;
}

/** runs 列表列（磁碟目錄為準、in-mem 快照 overlay 即時進度）。 */
export interface PromptDebugBatchRunRow {
  run_id: string;
  /** 屬於哪個多模型並行群組；空字串＝單模型 run（見 `startPromptDebugBatchGroup`）。 */
  group_id: string;
  created_at: string;
  input_name: string;
  /** 本批用的 Prompt 版本名；空＝啟動前在頁面上臨時編輯過。 */
  prompt_version: string;
  /** 版本口徑軌跡（release / draft / 臨時編輯）。 */
  prompt_kind: string;
  model: string;
  /** 本批用的模型配置名（**啟動當下的名字快照**，非 id——配置日後改名/刪除，歷史 run 仍讀得懂）。
   * 空＝改造前的舊 run 或腳本直呼，此時只有 model 可追。 */
  config_name: string;
  limit: number;
  /** 本批解析後的併發 ceiling（稽核用事實紀錄）；執行期 AIMD governor 只在其下調整。
   * `null`＝改造前的舊 run 沒記這欄。 */
  workers: number | null;
  status: PromptDebugBatchStatus;
  /** `null`＝未知（磁碟推導的中斷 run）；顯示時要區分於 0。 */
  total: number | null;
  resumed: number;
  processed: number;
  /** 累計成功**筆數**。 */
  ok_count: number;
  failed: number;
  invalid: number;
  cost_usd: number;
  /** 本次執行段落秒數；`null`＝未知（改造前的舊 run／中斷推導）。 */
  elapsed_sec: number | null;
  /** 含歷次續跑的累計執行秒數（列表顯示這個）。 */
  elapsed_total_sec: number | null;
  /** 執行過幾段（1＝一次跑完，>1＝中途停過再續跑）。 */
  session_count: number;
  has_csv: boolean;
}

/** run 產物下載類型：csv=結果表、preds=成功判定彙總、input=原輸入檔。 */
export type PromptDebugBatchFileKind = 'csv' | 'preds' | 'input';

// 前端跑批一律走 `startPromptDebugBatchGroup`（單選一個 model＝群組大小為 1），不維護第二條
// 啟動路徑。後端 `POST /batch/start` 端點保留供腳本/外部呼叫，前端無呼叫端。

/** 全部跑批 run 摘要（新→舊）。 */
export const listPromptDebugBatchRuns = (): Promise<{ runs: PromptDebugBatchRunRow[] }> =>
  j<{ runs: PromptDebugBatchRunRow[] }>(`${BASE}/v1/prejudge/prompt-debug/batch/runs`);

/** 一筆送去跑批的模型配置（具名配置攤平後的旋鈕；後端據此逐筆各起一個獨立 run）。 */
export interface PromptDebugBatchConfigEntry {
  /** 配置名（全域唯一）；後端寫進 manifest 供事後追溯「那批是用哪個設定跑的」。 */
  config_name: string;
  provider: string;
  model: string;
  thinking?: string;
  reasoning_effort?: string;
  temperature?: number | null;
}

/** 啟動多配置並行跑批的參數：與單筆共用輸入/Prompt/範圍，`configs` 逐筆自帶完整旋鈕。 */
export interface PromptDebugBatchGroupStartPayload {
  /** 上傳的輸入檔；`null`＝改走 DB 取數（需帶 `source` + `itemIds`）。 */
  file: File | null;
  /** 留空＝後端取版本庫最新版；三軌一致（單模型／多模型／單次調試共用同一份口徑來源）。 */
  systemPrompt: string;
  /** 反饋來源 id（DB 取數模式必填，見 `SOURCES` 常數）；`file` 有值時忽略。 */
  source?: string;
  /** DB 取數模式的自然鍵清單，換行分隔（如 session_oid）。 */
  itemIds?: string;
  sheet?: string;
  idColumn?: string;
  textColumn?: string;
  limit?: number;
  /** 併發 ceiling 覆寫；省略＝自動（依 model 查表 + 執行期 AIMD 自適應）。
   * 前端已不再送——沒有任何供應商公布「併發數」這個維度，填進去的數字必然是猜的。 */
  workers?: number;
  /**
   * 欲並行的模型配置（1–6 筆；後端 `_MAX_ENTRIES_PER_GROUP` 上限一致）。
   *
   * **是陣列不是「以 model 為 key 的 map」**：兩筆配置完全可能用同一個 model 只差旋鈕
   * （`gpt-5.4-mini · medium` vs `· high` 正是具名配置最典型的用途），以 model 當 key 會讓後一筆
   * 靜默覆蓋前一筆。也因為每筆自帶完整旋鈕，不再有「全體共用一組 overrides」的限制。
   */
  configs: PromptDebugBatchConfigEntry[];
}

/**
 * 單一配置的啟動結果：`started=false` 時只有 `error`，不含 run_id（該筆的 run 從未建立）。
 *
 * ⚠️ 欄位叫 `started` 而不是 `ok`，而且成員**刻意不帶初始快照**——這兩件事是同一個事故的產物：
 * 後端曾寫 `{**ident, "ok": True, **snapshot}`，快照自帶的 `ok`（累計成功筆數，新 run 恆為 0）
 * 把布林旗標吃掉，於是每個**成功**啟動的配置都被判成失敗、跳紅色 toast，連群組進度輪詢都被
 * 擋在 `if (ok.length)` 後面從未執行。即時進度一律走輪詢（`getPromptDebugBatchGroup`）。
 *
 * `model`/`provider` 是「實際跑了什麼」的事實紀錄，與 `config_name`（用哪個設定跑的）語義不同，
 * 刻意不合併——同一個 model 可能來自兩筆不同配置。
 */
export interface PromptDebugBatchGroupMember {
  config_name: string;
  model: string;
  provider: string;
  /** 這一筆有沒有成功**啟動**（不是有沒有跑成功——後者看輪詢回來的 run status）。 */
  started: boolean;
  /** 啟動當下的 run 狀態；`started=false` 時不存在。 */
  status?: PromptDebugBatchStatus;
  run_id?: string;
  error?: string;
}

/** 多模型並行的啟動結果（`POST /batch/start-multi`）。 */
export interface PromptDebugBatchGroupResult {
  group_id: string;
  created_at: string;
  members: PromptDebugBatchGroupMember[];
  /** DB 取數模式才有：要了幾筆、撈到幾筆、幾筆查無、幾筆內容為空。
   * 必須回報給使用者——貼了 1000 個 id 只跑了 940 筆，不講清楚就只會看到「總數對不上」。 */
  db_stats?: {
    requested: number;
    found: number;
    missing: number;
    empty_conversations: number;
    valid_rows: number;
  };
}

/**
 * 啟動多配置並行跑批：同一份輸入 × 同一份 Prompt，在每筆模型配置上各自獨立起一個完整的 run。
 *
 * Provider 是配置的顯式屬性、不用從 model 名猜——附帶鬆綁：自訂／未登記於 llm_model.json 的
 * model 名，第一次能合法用在多模型跑批。缺 token 這類問題仍由後端在啟動前逐筆攔下。
 */
export const startPromptDebugBatchGroup = (
  payload: PromptDebugBatchGroupStartPayload,
): Promise<PromptDebugBatchGroupResult> => {
  const fd = new FormData();
  // 兩種輸入二選一：有檔案＝上傳模式；否則走 DB 取數（後端撈完會落成 CSV 快照，之後路徑同構）
  if (payload.file) {
    fd.append('file', payload.file);
  } else {
    fd.append('source', payload.source ?? '');
    fd.append('item_ids', payload.itemIds ?? '');
  }
  fd.append('system_prompt', payload.systemPrompt);
  if (payload.sheet) fd.append('sheet', payload.sheet);
  if (payload.idColumn) fd.append('id_column', payload.idColumn);
  if (payload.textColumn) fd.append('text_column', payload.textColumn);
  fd.append('limit', String(payload.limit ?? 0));
  // 0＝交給後端自動解析（見 `workers` 欄註解）
  fd.append('workers', String(payload.workers ?? 0));
  fd.append('configs', JSON.stringify(payload.configs));
  return j<PromptDebugBatchGroupResult>(`${BASE}/v1/prejudge/prompt-debug/batch/start-multi`, {
    method: 'POST',
    body: fd,
  });
};

/** 單一多模型群組內所有 member run 的摘要（輪詢用；同 `listPromptDebugBatchRuns` 形狀，已按 group 過濾）。 */
export const getPromptDebugBatchGroup = (
  groupId: string,
): Promise<{ group_id: string; runs: PromptDebugBatchRunRow[] }> =>
  j<{ group_id: string; runs: PromptDebugBatchRunRow[] }>(
    `${BASE}/v1/prejudge/prompt-debug/batch/groups/${encodeURIComponent(groupId)}`,
  );

/** 單 run 進度輪詢。 */
export const getPromptDebugBatchRun = (runId: string): Promise<PromptDebugBatchSnapshot> =>
  j<PromptDebugBatchSnapshot>(
    `${BASE}/v1/prejudge/prompt-debug/batch/runs/${encodeURIComponent(runId)}`,
  );

/** 停止執行中 run（已完成筆保留為斷點，可事後續跑）。 */
export const cancelPromptDebugBatchRun = (runId: string): Promise<{ ok: boolean }> =>
  j<{ ok: boolean }>(
    `${BASE}/v1/prejudge/prompt-debug/batch/runs/${encodeURIComponent(runId)}/cancel`,
    { method: 'POST' },
  );

/**
 * 續跑（只補未成功筆）或強制重跑（rerun=true 忽略斷點全部重打）。
 * manifest 鎖輸入/Prompt/schema/model：SSOT 或功能區 model 變了後端會拒絕（400 附原因）。
 */
export const resumePromptDebugBatchRun = (
  runId: string,
  options: { workers?: number; rerun?: boolean } = {},
): Promise<PromptDebugBatchSnapshot> =>
  j<PromptDebugBatchSnapshot>(
    `${BASE}/v1/prejudge/prompt-debug/batch/runs/${encodeURIComponent(runId)}/resume`,
    { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(options) },
  );

/**
 * 取回 run 產物 blob（帶 auth header，交由呼叫端觸發另存）。
 * @throws {Error} 檔案尚未產生或 run 不存在（404 detail）。
 */
export const downloadPromptDebugBatchFile = async (
  runId: string,
  kind: PromptDebugBatchFileKind,
): Promise<Blob> => {
  const token = getToken();
  const res = await fetch(
    `${BASE}/v1/prejudge/prompt-debug/batch/runs/${encodeURIComponent(runId)}/files/${kind}`,
    { headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}) } },
  );
  if (!res.ok) {
    let message = `下載失敗 ${res.status}`;
    try {
      const payload = await res.json();
      if (typeof payload?.detail === 'string') message = payload.detail;
    } catch {
      /* 沿用 HTTP status */
    }
    throw new Error(message);
  }
  return res.blob();
};

function dispatchFrame({ event, payload }: SseFrame, handlers: PromptDebugHandlers): void {
  if (payload === null) {
    handlers.onError?.('收到無法解析的 SSE 事件');
    return;
  }
  if (event === 'meta') handlers.onMeta?.(payload as unknown as PromptDebugMeta);
  else if (event === 'delta') handlers.onDelta?.(String(payload.text ?? ''));
  else if (event === 'warning') handlers.onWarning?.(String(payload.message ?? ''));
  else if (event === 'result') handlers.onResult?.(payload as unknown as PromptDebugResult);
  else if (event === 'usage') handlers.onUsage?.(payload as unknown as PromptDebugUsage);
  else if (event === 'error') handlers.onError?.(String(payload.message ?? '未知錯誤'));
  else if (event === 'done') handlers.onDone?.();
}
