// 模型配置的「規格鍵」與「衍生名稱」——與後端 `settings.spec_key` / `derive_config_name` 鏡像。
//
// 前端這份的用途**只有兩個**：手風琴 header 的即時預覽、儲存前的本地撞號偵測。
// 落庫與最終顯示一律以後端回傳為準（`all_model_configs()` 會補上權威的 name），所以前後端若有
// 落差，最壞情況是預覽字串差一點，不會造成資料錯誤。
// 前後端同構雙寫是本 repo 的既有明示慣例（`capabilitiesFor`↔`model_capabilities_for`、
// `defaultModelFor`↔`default_model_for`、`providerIdForModel`↔`provider_id_for_model`）。
import { capabilitiesFor, PROVIDERS } from '../constants';
import type { LlmModelConfig } from '../types';

/** 一筆配置壓縮後的規格鍵（對齊後端 `ModelConfigKey`）。 */
export type ModelConfigKey = readonly [string, string, string, string, number | null];

/**
 * 把一筆配置壓成「實際會送出的規格」——唯一性判定與衍生名的共同輸入。
 *
 * 只折**可證明惰性**的旋鈕，判別軸＝配置自帶的 `provider`（與後端、與 `client._reasoning_kwargs`
 * 同軸）：
 * - R1 effortOnly（OpenAI/Gemini）：執行層根本不讀 thinking → 折成 `default`
 * - R2 nativeSwitch（ByteDance）且 thinking 為 disabled/auto：執行層不送 reasoning_effort → 折 `default`
 * - R3 temperature：**該組合下 API 不接受自訂溫度時**折成 `null`（送了也沒用，只接受預設值），
 *   否則保留、只 `round(2)`。⚠️ 未被鎖的 model 會真的採用該值（ByteDance seed 系列實測受理 0.3），
 *   對它們折疊等於改變送出內容。判定用**折疊後**的 thinking/effort，順序不可顛倒
 *
 * @param cfg 一筆模型配置（至少需 provider/model）。
 * @returns 規格鍵 tuple；比較請用 {@link specKeyOf} 轉字串或逐項比對。
 */
export function specKey(cfg: Partial<LlmModelConfig>): ModelConfigKey {
  const provider = (cfg.provider ?? '').trim();
  const model = (cfg.model ?? '').trim();
  let thinking = cfg.thinking || 'default';
  let effort = cfg.reasoning_effort || 'default';

  const cap = capabilitiesFor(model, provider);
  const native = cap.thinkingControl === 'nativeSwitch';
  if (!native) thinking = 'default';
  else if (thinking === 'disabled' || thinking === 'auto') effort = 'default';

  const t = cfg.temperature;
  let temperature = t === null || t === undefined ? null : Math.round(t * 100) / 100;
  if (temperature !== null && temperatureIsInert(cap, native, thinking, effort)) temperature = null;
  return [provider, model, thinking, effort, temperature] as const;
}

/**
 * 該組合下自訂 temperature 是否**送了也沒用**（API 只接受預設值）——與 `LlmKnobs` 的 `tempLocked`
 * 及後端 `_temperature_is_inert` 同一套判定。
 *
 * @param cap `capabilitiesFor()` 的結果
 * @param native 是否 nativeSwitch 供應商（避免重算）
 * @param thinking **已折疊**的 thinking
 * @param effort **已折疊**的 reasoning_effort
 */
function temperatureIsInert(
  cap: ReturnType<typeof capabilitiesFor>,
  native: boolean,
  thinking: string,
  effort: string,
): boolean {
  if (cap.temperatureAlwaysLocked) return true;
  const reasoningActive = native
    ? thinking === 'enabled' || thinking === 'auto'
    : effort !== 'none' && effort !== 'default';
  return reasoningActive && cap.temperatureLockedWhenThinking;
}

/** 規格鍵的字串形式，供 `Set`/`Map` 當鍵用（tuple 在 JS 沒有值相等語義）。 */
export const specKeyOf = (cfg: Partial<LlmModelConfig>): string => JSON.stringify(specKey(cfg));

/**
 * 由規格衍生配置名——「名稱就是它的規格」，使用者不能也不需要自己取名。
 *
 * 版面：`{供應商短標} · {model}[ · thinking:{值}][ · {推理檔位}][ · temp:{值}]`
 * 旋鈕值一律用 API 原始英文字面，不做中文化——名稱要能對得上官方文件與錯誤訊息。
 * thinking 加 `thinking:` 前綴以與同為裸 enum 的推理檔位區分。
 * 折疊後為 default／null 的維度整段不出現。provider 必須進名稱——`gpt-oss-120b-250805` 掛在
 * ByteDance 底下但名字是 `gpt-` 開頭，且 provider 決定打哪個端點、用誰的 token。
 *
 * ⚠️ 名稱是「**宣告**規格」不是「保證實跑值」：執行層對 400 有 reasoning_effort 自動降級。
 *
 * @param cfg 一筆模型配置。
 * @returns 顯示用名稱；provider/model 皆空時回空字串（新建草稿未填完的中間態）。
 */
export function deriveConfigName(cfg: Partial<LlmModelConfig>): string {
  const [provider, model, thinking, effort, temperature] = specKey(cfg);
  if (!provider && !model) return '';
  const hit = PROVIDERS.find((p) => p.id === provider);
  const parts: string[] = [hit?.short_label || provider || '未知供應商', model];
  if (thinking !== 'default') parts.push(`thinking:${thinking}`);
  if (effort !== 'default') parts.push(effort);
  // ⚠️ 必須顯式比對 null/undefined——0 是合法溫度，falsy 判斷會把它漏掉
  if (temperature !== null) parts.push(`temp:${temperature}`);
  return parts.filter(Boolean).join(' · ');
}
