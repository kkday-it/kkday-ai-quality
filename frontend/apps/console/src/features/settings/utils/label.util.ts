// 設定 config 預設名稱用的時間戳。QC DB config 仍以時間戳作唯一標籤（例「QC DB 202606301458」）。
// LLM config 改用「參數拼接名」（composeLlmLabel），不再手動命名。

/** 本地時區時間戳 YYYYMMDDHHmm（config 預設名稱用）。 */
export function configStamp(d: Date = new Date()): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}${p(d.getHours())}${p(d.getMinutes())}`;
}

/** provider id → 顯示名（拼接名稱用；比 PROVIDERS.label「GPT (OpenAI)」精簡）。 */
const PROVIDER_DISPLAY: Record<string, string> = {
  openai: 'OpenAI',
  gemini: 'Gemini',
  bytedance: 'ByteDance',
};

/**
 * 由 config 參數拼接 LLM 顯示名（取代手動命名）：`<Provider> <model> <reasoning_effort>`，
 * 例「OpenAI gpt-5-nano medium」。temperature / thinking 對 gpt-5 系列鎖定或由 reasoning 主導，
 * 拼入只會讓每張卡雷同，故不納入；reasoning_effort 為 default/空時省略。
 *
 * @param c 取 provider / model / reasoning_effort 三欄
 * @returns 空格分隔的顯示名
 */
export function composeLlmLabel(c: {
  provider: string;
  model: string;
  reasoning_effort: string;
}): string {
  const prov = PROVIDER_DISPLAY[c.provider] ?? c.provider;
  const effort = c.reasoning_effort && c.reasoning_effort !== 'default' ? c.reasoning_effort : '';
  return [prov, c.model, effort].filter(Boolean).join(' ');
}
