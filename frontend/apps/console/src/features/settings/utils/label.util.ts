// LLM 顯示名參數拼接（composeLlmLabel）：連線層改按 provider/env 為 key（無需手動命名/時間戳）。
import { PROVIDERS } from '../constants';

/** provider id → 精簡顯示名（SSOT＝llm_model.json providers[].short_label，經 PROVIDERS 帶入；不再前端各寫一份）。 */
const PROVIDER_DISPLAY: Record<string, string> = Object.fromEntries(
  PROVIDERS.map((p) => [p.id, p.short_label ?? p.label]),
);

/**
 * 由 config 參數拼接 LLM 顯示名（取代手動命名）：`<Provider> <model> <reasoning_effort>`，
 * 例「OpenAI gpt-5-nano medium」。temperature 對 gpt-5 系列鎖定，拼入只會讓每張卡雷同，故不納入；
 * reasoning_effort 為 default/空時省略。
 *
 * 「不推理」判定需同時兼顧兩種供應商控制形態（見 capabilitiesFor.thinkingControl，2026-07-23 重寫）：
 * ByteDance 等 nativeSwitch 供應商靠 `thinking==='disabled'` 這個真實原生開關；OpenAI/Gemini 等
 * effortOnly 供應商沒有獨立開關，`reasoning_effort==='none'` 本身就是「不推理」的完整表示。
 *
 * @param c 取 provider / model / reasoning_effort（+ 選填 thinking）欄位
 * @returns 空格分隔的顯示名
 */
export function composeLlmLabel(c: {
  provider: string;
  model: string;
  reasoning_effort: string;
  thinking?: string;
}): string {
  const prov = PROVIDER_DISPLAY[c.provider] ?? c.provider;
  const noThinking = c.thinking === 'disabled' || c.reasoning_effort === 'none';
  const effort = noThinking
    ? 'no-thinking'
    : c.reasoning_effort && c.reasoning_effort !== 'default'
      ? c.reasoning_effort
      : '';
  return [prov, c.model, effort].filter(Boolean).join(' ');
}
