// 設定領域 API：LLM 模型配置讀寫。
import { BASE, JSON_HEADERS, j } from './http.api';
import type { SettingsBundle } from '@/features/settings/types';

/** 讀 LLM 模型配置（api_token 已遮罩，附 has_token / stub_mode）。 */
export const getSettings = (): Promise<SettingsBundle> => j<SettingsBundle>(`${BASE}/settings`);

/** 讀完整配置（api_token 明文，供設定面板眼睛切換顯示全文）。⚠️ 僅限受信任內網。 */
export const getSettingsRaw = (): Promise<SettingsBundle> =>
  j<SettingsBundle>(`${BASE}/settings/raw`);

/** 儲存 LLM 模型配置（空/遮罩 token 不覆蓋既有）→ 權威回應（同 getSettings 形狀）。 */
export const saveSettings = (patch: Record<string, unknown>): Promise<SettingsBundle> =>
  j<SettingsBundle>(`${BASE}/settings`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(patch),
  });

/** 即時 LLM 連線測試結果（用當前表單值送極短 prompt，不寫入）。 */
export interface LlmPingResult {
  ok: boolean;
  model?: string;
  base_url?: string;
  sent?: string;
  reply?: string;
  latency_ms?: number;
  tokens?: number;
  error?: string;
}

/** 即時測試 LLM 連線：用「當前表單值」送極短 prompt（不寫入）。 */
export const testLlm = (cfg: Record<string, unknown> = {}): Promise<LlmPingResult> =>
  j<LlmPingResult>(`${BASE}/settings/test-llm`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(cfg),
  });
