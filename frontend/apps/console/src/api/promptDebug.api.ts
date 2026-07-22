// 售後根因 Prompt 調試台：預設資料 + POST SSE 串流 client。
import { BASE, getToken, j, JSON_HEADERS } from './http.api';
import type { LlmOverrides } from '@/features/settings/types';

export interface PromptDebugDefaults {
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
