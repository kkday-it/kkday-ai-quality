// 售後根因 Prompt 調試台：預設資料 + POST SSE 串流 client + 人工評判案例庫 + AI 定點改寫。
import { BASE, getToken, j, JSON_HEADERS, postSse, type SseFrame } from './http.api';
import type { LlmOverrides } from '@/features/settings/types';

export interface PromptDebugDefaults {
  /** 目前線上口徑＝版本庫最新版檔名（YYYY-MM-DD-HHMMSS）。 */
  prompt_version: string;
  /** 版本庫全部版本名（新→舊）；僅供顯示，頁面不提供切換。 */
  prompt_versions: string[];
  /** 最新版 system prompt 全文。 */
  system_prompt: string;
  output_schema: Record<string, unknown>;
  output_fields: Array<{
    key: string;
    label: string;
    hint: string;
  }>;
  taxonomy_version: string;
  category_count: number;
  theme_count: number;
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
  versions: string[];
}

/**
 * 把編輯後的 Prompt 存成新的時間戳版本檔，存完即成為線上最新版（單次調試與跑批同步生效）。
 * @param systemPrompt 要存檔的 system prompt 全文
 */
export const savePromptVersion = (systemPrompt: string): Promise<PromptVersionSaved> =>
  j<PromptVersionSaved>(`${BASE}/v1/prejudge/prompt-debug/prompt-versions`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ system_prompt: systemPrompt }),
  });

// ── 人工評判案例庫（/prompt-debug/reviews）───────────────────────────────────

/** 案例庫列表列；對話原文只給前 200 字預覽，全文由後端改寫/回歸端點自行按 id 取。 */
export interface PromptDebugReviewRow {
  id: number;
  conversation_preview: string;
  conversation_chars: number;
  /** AI 當時判定的全部欄位。 */
  ai_output: Record<string, unknown>;
  /** 人標的正解 `{欄名: 正解值}`；只含被標錯的欄，`{}`＝全欄皆對（正例）。 */
  corrections: Record<string, unknown>;
  /** 人明確標「對」的欄名；回歸時這些欄不准變。兩者都沒出現的欄＝沒看過，不計分。 */
  confirmed: string[];
  comment: string;
  /** 當時的線上 Prompt 版本；空＝送出前臨時編輯過。 */
  prompt_version: string;
  model: string;
  reviewer: string;
  created_at: string;
}

/** 新增案例的送出內容。 */
export interface PromptDebugReviewPayload {
  conversation: string;
  ai_output: Record<string, unknown>;
  corrections: Record<string, unknown>;
  /** 人明確標「對」的欄名（不得與 corrections 重疊，後端會擋）。 */
  confirmed: string[];
  comment?: string;
  prompt_version?: string;
  model?: string;
}

/** 案例庫列表（新→舊）。 */
export const listPromptDebugReviews = (): Promise<{ reviews: PromptDebugReviewRow[] }> =>
  j<{ reviews: PromptDebugReviewRow[] }>(`${BASE}/v1/prejudge/prompt-debug/reviews`);

/** 存一則人工評判案例；回新案例 id。 */
export const createPromptDebugReview = (
  payload: PromptDebugReviewPayload,
): Promise<{ id: number }> =>
  j<{ id: number }>(`${BASE}/v1/prejudge/prompt-debug/reviews`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(payload),
  });

/** 刪一則案例。 */
export const deletePromptDebugReview = (id: number): Promise<{ ok: boolean }> =>
  j<{ ok: boolean }>(`${BASE}/v1/prejudge/prompt-debug/reviews/${id}`, { method: 'DELETE' });

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
  onResult?: (payload: PromptReviseResult) => void;
  onUsage?: (payload: PromptDebugUsage) => void;
  onError?: (message: string) => void;
  onDone?: () => void;
}

/** 依選中案例串流產出定點補丁；`systemPrompt` 留空＝用版本庫最新版。 */
export const streamPromptRevise = (
  body: { review_ids: number[]; system_prompt: string; overrides?: LlmOverrides },
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
  review_ids: number[];
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
  | 'running'
  | 'cancelling'
  | 'done'
  | 'error'
  | 'cancelled'
  | 'interrupted';

/** 快照內「最近完成」明細（即時回報環，全量明細在 jsonl 下載）。 */
export interface PromptDebugBatchRecentItem {
  item_id: string;
  ok: boolean;
  theme: string | null;
  category: string | null;
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
  model: string;
  input_name: string;
  created_at: string;
  /** 本次選中目標（offset/limit 後）。 */
  total: number;
  /** 斷點復用的成功筆。 */
  resumed: number;
  /** 本次實際要請求的筆數。 */
  pending: number;
  /** 本次已完成請求數（成功+失敗）。 */
  processed: number;
  /** 累計成功（含復用）。 */
  ok: number;
  failed: number;
  /** 成功但欄位校驗未過（詳情在 jsonl.validation_issues）。 */
  invalid: number;
  total_tokens: number;
  cost_usd: number;
  /** 本次啟動 epoch 秒（前端算速度/ETA）。 */
  started_at?: number;
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
  created_at: string;
  input_name: string;
  /** 本批用的 Prompt 版本名；空＝啟動前在頁面上臨時編輯過。 */
  prompt_version: string;
  model: string;
  offset: number;
  limit: number;
  workers: number | null;
  status: PromptDebugBatchStatus;
  total: number;
  resumed: number;
  processed: number;
  ok: number;
  failed: number;
  invalid: number;
  cost_usd: number;
  has_csv: boolean;
}

/** 啟動跑批參數（file 之外的欄位缺省沿用後端預設）。 */
export interface PromptDebugBatchStartPayload {
  file: File;
  /** 留空＝後端取版本庫最新版。 */
  systemPrompt: string;
  /** XLSX 工作表名；空＝第一個工作表（CSV 忽略）。 */
  sheet?: string;
  idColumn?: string;
  textColumn?: string;
  offset?: number;
  /** 實際跑多少條；0＝全部。 */
  limit?: number;
  workers?: number;
  overrides?: LlmOverrides;
}

/** run 產物下載類型：csv=結果表、jsonl=逐筆原始紀錄（斷點）、preds=成功判定彙總、input=原輸入檔。 */
export type PromptDebugBatchFileKind = 'csv' | 'jsonl' | 'preds' | 'input';

/** 啟動批量跑批（multipart）；回初始進度快照（含 run_id）。 */
export const startPromptDebugBatch = (
  payload: PromptDebugBatchStartPayload,
): Promise<PromptDebugBatchSnapshot> => {
  const fd = new FormData();
  fd.append('file', payload.file);
  fd.append('system_prompt', payload.systemPrompt);
  if (payload.sheet) fd.append('sheet', payload.sheet);
  if (payload.idColumn) fd.append('id_column', payload.idColumn);
  if (payload.textColumn) fd.append('text_column', payload.textColumn);
  fd.append('offset', String(payload.offset ?? 0));
  fd.append('limit', String(payload.limit ?? 0));
  fd.append('workers', String(payload.workers ?? 8));
  if (payload.overrides) fd.append('overrides', JSON.stringify(payload.overrides));
  return j<PromptDebugBatchSnapshot>(`${BASE}/v1/prejudge/prompt-debug/batch/start`, {
    method: 'POST',
    body: fd,
  });
};

/** 全部跑批 run 摘要（新→舊）。 */
export const listPromptDebugBatchRuns = (): Promise<{ runs: PromptDebugBatchRunRow[] }> =>
  j<{ runs: PromptDebugBatchRunRow[] }>(`${BASE}/v1/prejudge/prompt-debug/batch/runs`);

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
