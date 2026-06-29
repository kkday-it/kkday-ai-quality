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

/** 即時測試 LLM 連線：用「當前表單值」送極短 prompt（不寫入），回 `{ ok, model, base_url, sent, reply, latency_ms, tokens?, error? }`。 */
export const testLlm = (cfg: Record<string, unknown> = {}) =>
  j(`${BASE}/settings/test-llm`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(cfg),
  });
