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
