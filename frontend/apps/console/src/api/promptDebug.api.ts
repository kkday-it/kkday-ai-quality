// 售後根因 Prompt 調試台：預設資料 + POST SSE 串流 client。
import { BASE, getToken, j, JSON_HEADERS } from './http.api';
import type { LlmOverrides } from '@/features/settings/types';

/** 輸出契約版本：v2=現行批次同款（urgency_flag 布林＋tail_theme）；v3=新規格（keywords 陣列＋urgency 1–5＋no_actionable_content＋n/a 哨兵）。 */
export type PromptDebugContractKey = 'v2' | 'v3';

export interface PromptDebugContract {
  key: PromptDebugContractKey;
  label: string;
  description: string;
  system_prompt: string;
  output_schema: Record<string, unknown>;
  output_fields: Array<{
    key: string;
    label: string;
    hint: string;
  }>;
}

export interface PromptDebugDefaults {
  default_contract: PromptDebugContractKey;
  contracts: Record<PromptDebugContractKey, PromptDebugContract>;
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
  system_prompt: string;
  /** 輸出契約版本；貼什麼契約的 Prompt 就選什麼，schema 與校驗隨之切換。 */
  contract: PromptDebugContractKey;
  /** 本次執行 LLM 覆寫（provider+旋鈕）；缺省沿用 prompt_debug 功能區默認。 */
  overrides?: LlmOverrides;
}

export interface PromptDebugMeta {
  job_id: string;
  contract: PromptDebugContractKey;
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

/** fetch + ReadableStream 解析 POST SSE；EventSource 不支援 POST body。 */
export async function streamPromptDebug(
  body: PromptDebugBody,
  handlers: PromptDebugHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const headers = new Headers(JSON_HEADERS);
  const token = getToken();
  if (token) headers.set('Authorization', `Bearer ${token}`);
  const response = await fetch(`${BASE}/v1/prejudge/prompt-debug/stream`, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const payload = await response.json();
      message = typeof payload?.detail === 'string' ? payload.detail : message;
    } catch {
      /* 沿用 HTTP status */
    }
    throw new Error(message);
  }
  const reader = response.body?.getReader();
  if (!reader) throw new Error('瀏覽器不支援串流回應');

  const decoder = new TextDecoder('utf-8');
  let buffer = '';
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n');
    let boundary = buffer.indexOf('\n\n');
    while (boundary >= 0) {
      dispatchFrame(buffer.slice(0, boundary), handlers);
      buffer = buffer.slice(boundary + 2);
      boundary = buffer.indexOf('\n\n');
    }
  }
  if (buffer.trim()) dispatchFrame(buffer, handlers);
}

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
  contract: PromptDebugContractKey;
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
  contract: PromptDebugContractKey;
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
  systemPrompt: string;
  contract: PromptDebugContractKey;
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
  fd.append('contract', payload.contract);
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

function dispatchFrame(frame: string, handlers: PromptDebugHandlers): void {
  let event = '';
  const data: string[] = [];
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim();
    else if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
  }
  if (!event) return;
  let payload: any;
  try {
    payload = JSON.parse(data.join('\n') || '{}');
  } catch {
    handlers.onError?.('收到無法解析的 SSE 事件');
    return;
  }
  if (event === 'meta') handlers.onMeta?.(payload as PromptDebugMeta);
  else if (event === 'delta') handlers.onDelta?.(String(payload.text ?? ''));
  else if (event === 'warning') handlers.onWarning?.(String(payload.message ?? ''));
  else if (event === 'result') handlers.onResult?.(payload as PromptDebugResult);
  else if (event === 'usage') handlers.onUsage?.(payload as PromptDebugUsage);
  else if (event === 'error') handlers.onError?.(String(payload.message ?? '未知錯誤'));
  else if (event === 'done') handlers.onDone?.();
}
