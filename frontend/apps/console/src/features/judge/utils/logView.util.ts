// 執行日誌渲染共用純函式 + 常數：供 `PrejudgeLogView`（流程 tab）與 `LlmCallTimeline`
// （LLM 調用 tab 內容）共用，避免同一組格式化邏輯在兩處各自維護一份。

/** epoch 秒 → HH:mm:ss（本地時區）。 */
export const fmtTs = (ts: number): string =>
  new Date(ts * 1000).toLocaleTimeString('en-GB', { hour12: false });

/** LLM 回應 raw 為 JSON 格式字串（schema/response_format 要求）時解析出物件供樹狀檢視；
 * 解析失敗（非 JSON 輸出 / 錯誤文字）回 null，模板改走純文字顯示，如實呈現原始內容。 */
export const tryParseRaw = (raw: unknown): unknown | null => {
  if (typeof raw !== 'string' || !raw.trim()) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
};

/** 請求參數 → 分離純量（平鋪）與物件（JSON 區塊），full kwargs 逐項不漏。 */
export const scalarParams = (data?: Record<string, unknown>) =>
  Object.entries(data ?? {}).filter(
    ([, v]) => v !== null && v !== undefined && v !== '' && typeof v !== 'object',
  );
export const objectParams = (data?: Record<string, unknown>) =>
  Object.entries(data ?? {}).filter(([, v]) => v !== null && typeof v === 'object');

/** LLM 調用 tab 內單一時間軸節點的錨點 id（`noInnerScroll` 左側導航 href 目標）。 */
export const logEntryId = (callKey: string, i: number): string =>
  `log-entry-${callKey}-${i}`.replace(/[^a-zA-Z0-9_-]/g, '_');

/** LLM 調用時間軸節點色（依 kind 語義）。 */
export const LLM_DOT: Record<string, string> = {
  llm_request: '#4080ff',
  llm_prompt: '#86909c',
  llm_response: '#00b42a',
  llm_note: '#ff7d00',
  error: '#f53f3f',
};

/** LLM 調用時間軸節點 kind 機器碼 → 中文顯示標籤。 */
export const LLM_KIND_LABEL: Record<string, string> = {
  llm_request: 'LLM 請求',
  llm_prompt: 'Prompt 全文',
  llm_response: 'LLM 輸出',
  llm_note: '降級註記',
};
