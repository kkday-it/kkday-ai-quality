// 設定領域 API：LLM 模型配置讀寫。
import { BASE, JSON_HEADERS, j } from './http.api';

/** 讀 LLM 模型配置（api_token 已遮罩，附 has_token / stub_mode）。 */
export const getSettings = () => j(`${BASE}/settings`);

/** 讀完整配置（api_token 明文，供設定面板眼睛切換顯示全文）。⚠️ 僅限受信任內網。 */
export const getSettingsRaw = () => j(`${BASE}/settings/raw`);

/** 儲存 LLM 模型配置（空/遮罩 token 不覆蓋既有）。 */
export const saveSettings = (patch: Record<string, unknown>) =>
  j(`${BASE}/settings`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(patch),
  });

/** 測試 LLM 連線：以已儲存設定送極短 prompt，回 `{ ok, model, sent, reply, latency_ms, tokens?, error? }`。 */
export const testLlm = () =>
  j(`${BASE}/settings/test-llm`, { method: 'POST', headers: JSON_HEADERS, body: '{}' });

/** 動態列出當前配置可用的 model id（後端已過濾 ≥ 門檻版本）；無 token 回 `{ models: [] }`。 */
export const listModels = (): Promise<{ models: string[] }> => j(`${BASE}/settings/models`);
