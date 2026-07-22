// LLM 顯示名參數拼接（composeLlmLabel）：連線層改按 provider/env 為 key（無需手動命名/時間戳）。
import { PROVIDERS } from '../constants';

/** provider id → 精簡顯示名（SSOT＝llm_model.json providers[].short_label，經 PROVIDERS 帶入；不再前端各寫一份）。 */
const PROVIDER_DISPLAY: Record<string, string> = Object.fromEntries(
  PROVIDERS.map((p) => [p.id, p.short_label ?? p.label]),
);

/**
 * 由 config 參數拼接 LLM 顯示名（取代手動命名）：`<Provider> <model> <reasoning_effort>`，
 * 例「OpenAI gpt-5-nano medium」。temperature 對 gpt-5 系列鎖定，拼入只會讓每張卡雷同，故不納入；
 * reasoning_effort 為 default/空時省略。thinking 關閉時 effort 實際不會送出 → 以 no-thinking 取代，避免誤導。
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
  const effort =
    c.thinking === 'off'
      ? 'no-thinking'
      : c.reasoning_effort && c.reasoning_effort !== 'default'
        ? c.reasoning_effort
        : '';
  return [prov, c.model, effort].filter(Boolean).join(' ');
}
