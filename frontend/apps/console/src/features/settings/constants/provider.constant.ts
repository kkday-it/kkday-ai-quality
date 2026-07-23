// LLM 供應商定義與設定面板常數。
// 供應商目錄 / reasoning 選項的「資料」來自 repo 根 config/global/llm_model.json（跨語言共用單一真相源）；
// 本檔僅保留型別與前端衍生預設，不再寫死 base_url / model 清單。

import llm from '@config/global/llm_model.json';

/** 下拉一個 model 選項：id + 質性描述（成本/用途 hint，內聚於各 model，不另立 modelMeta map）。 */
export interface ModelOption {
  id: string;
  desc?: string;
}

export interface Provider {
  id: string;
  label: string;
  /** 精簡顯示名（拼接 LLM config 名用；比 label「GPT (OpenAI)」短）。SSOT＝llm_model.json providers[].short_label。 */
  short_label?: string;
  base_url: string;
  /** 一律留空、不寫死於原始碼（不入 git / 不進 bundle），由使用者於面板填入後存後端。 */
  api_token?: string;
  /** 預設選中的 model id（與 defaultModels 排序解耦；缺省則回退 defaultModels[0].id）。 */
  defaultModel?: string;
  /** model 下拉清單；{ id, desc } 物件、排序由省到貴（成本低者在前，預設選最省）。 */
  defaultModels: ModelOption[];
  /** 該供應商是否有任何推理能力（provider 級能力預設；modelCapabilities 可對個別 model 覆寫）。 */
  supportsThinking?: boolean;
  /** thinking 控制形態：'effortOnly'＝無獨立開關，reasoning_effort 本身即完整控制面（OpenAI/Gemini，
   * 官方文件證實無此參數）；'nativeSwitch'＝有真實原生 thinking 開關（ByteDance/Ark，見 thinkingModes）。 */
  thinkingControl?: 'effortOnly' | 'nativeSwitch';
  /** nativeSwitch 供應商的可用狀態（如 ['enabled','disabled']，個別 model 可能多一個 'auto'）。 */
  thinkingModes?: string[];
  /** 該供應商可用的 reasoning_effort 值域（取代舊固定全域 REASONING）。 */
  reasoningEffortOptions?: string[];
  /** reasoning_effort 非 none 時是否鎖定 temperature（OpenAI reasoning model 為 true；Gemini/ByteDance 為 false）。 */
  temperatureLockedWhenThinking?: boolean;
  /** 不論 thinking 狀態、伺服器端一律忽略自訂 temperature（與上者為不同機制）；目前僅個別 model 覆寫，見 modelCapabilities。 */
  temperatureAlwaysLocked?: boolean;
  /** 鎖定時實際送出的 temperature 值（通常 1）。 */
  lockedTemperatureValue?: number;
  /** 該供應商 API 官方 temperature 值域上限（三供應商皆為 2；見 llm_model.json 註解）。 */
  maxTemperature?: number;
  /** thinking 關閉時的說明文案；僅 nativeSwitch 供應商有值（effortOnly 沒有「關閉」這個獨立狀態）。 */
  reasoningOffHint?: string;
  /** 官方文件連結（label → URL），供 UI 附連結直接跳轉核驗規則來源。 */
  docs?: Record<string, string>;
}

/**
 * 供應商定義：選供應商一次帶入 base_url 與該供應商的 model 清單。
 * 資料源＝config/global/llm_model.json（GPT model id 對齊 OpenAI 官方 gpt-5.5 / gpt-5.4 / mini / nano）。
 * JSON 字面型別與 Provider[] 結構相容（api_token 選填、不在 JSON 中），以 cast 收斂；`docs` 各供應商
 * 鍵名不同（各自的官方文件 label），TS 對 JSON 字面推斷出的型別是逐供應商各自精確的 key union，與
 * `Record<string, string>` 重疊度不足以直接 cast，故先過 `unknown`（TS2352 建議的標準解法）。
 */
export const PROVIDERS = llm.providers as unknown as Provider[];

/** reasoning_effort 完整值域（跨三供應商聯集，非單一 model 的實際支援值）；UI 恆用此清單畫按鈕，
 * 個別 model 不支援的值另用 capabilities.reasoningEffortOptions 算 disabled，不直接從清單移除
 * （避免同一控件在不同 model 間版位跳動）。資料源＝config/global/llm_model.json。 */
export const REASONING: string[] = llm.reasoning;

/** thinking 開關完整值域（對齊 Ark 官方 thinking.type 三態）；UI 恆用此清單畫按鈕，個別 model
 * 不支援的模式另用 capabilities.thinkingModes 算 disabled。資料源＝config/global/llm_model.json。 */
export const ALL_THINKING_MODES: string[] = llm.thinkingModes ?? ['enabled', 'disabled', 'auto'];

/** Model 下拉最低版本門檻（僅 gpt-* 受限）；動態 API 清單與 curated 顯示皆以此過濾。 */
export const MODEL_MIN_VERSION: string = llm.modelMinVersion;

/** LLM 消費功能區清單（prejudge/prompt_debug/sandbox）；資料源＝config/global/llm_model.json areas[]。 */
export const LLM_AREAS: string[] = llm.areas ?? ['prejudge', 'prompt_debug', 'sandbox'];

/** 每 model 可配參數能力（thinking 控制形態 / reasoning_effort 值域 / temperature 鎖定規則）。 */
export interface ModelCapability {
  supportsThinking: boolean;
  thinkingControl: 'effortOnly' | 'nativeSwitch';
  thinkingModes: string[];
  reasoningEffortOptions: string[];
  temperatureLockedWhenThinking: boolean;
  temperatureAlwaysLocked: boolean;
  lockedTemperatureValue: number;
  maxTemperature: number;
  reasoningOffHint: string;
  docs: Record<string, string>;
}

/** 個別 model 覆寫（優先於所屬 provider 級預設）；資料源＝config/global/llm_model.json modelCapabilities。 */
const MODEL_CAPABILITY_OVERRIDES: Record<string, Partial<ModelCapability>> =
  (llm as { modelCapabilities?: Record<string, Partial<ModelCapability>> }).modelCapabilities ?? {};

/**
 * 回某 model 的可配參數能力：預設取「該 model 所屬 provider」的 provider 級欄位，
 * `modelCapabilities[model_id]` 可對個別 model 覆寫任一欄位。取代舊寫死的
 * `tempLocked = provider === 'openai'` 與固定全域 REASONING 值域，與後端
 * `settings.model_capabilities_for()` 同一份資料源、同一套判定。
 * @param modelId LLM model id（如 gpt-5.4-mini）。
 * @param provider 該 model 所屬 provider id（缺省時反查所有 provider 的 defaultModels）。
 */
export function capabilitiesFor(modelId: string, provider?: string): ModelCapability {
  const owner =
    (provider && PROVIDERS.find((p) => p.id === provider)) ||
    PROVIDERS.find((p) => (p.defaultModels ?? []).some((m) => m.id === modelId)) ||
    PROVIDERS.find((p) => p.id === 'openai');
  const base: ModelCapability = {
    supportsThinking: owner?.supportsThinking ?? true,
    thinkingControl: owner?.thinkingControl ?? 'effortOnly',
    thinkingModes: owner?.thinkingModes ?? [],
    reasoningEffortOptions: owner?.reasoningEffortOptions ?? REASONING,
    temperatureLockedWhenThinking: owner?.temperatureLockedWhenThinking ?? false,
    temperatureAlwaysLocked: owner?.temperatureAlwaysLocked ?? false,
    lockedTemperatureValue: owner?.lockedTemperatureValue ?? 1,
    maxTemperature: owner?.maxTemperature ?? 2,
    reasoningOffHint: owner?.reasoningOffHint ?? '',
    docs: owner?.docs ?? {},
  };
  return { ...base, ...MODEL_CAPABILITY_OVERRIDES[modelId] };
}

/** 回某供應商切換時應帶入的預設 model id（provider 自帶 defaultModel，缺省則取 defaultModels 首筆）。
 * 供 UI 切換供應商時同步重置 model，避免殘留另一供應商的 model id（見 useLlmAreaDefault.setProvider）。
 * @param providerId 供應商 id（如 openai/gemini/bytedance）。
 */
export function defaultModelFor(providerId: string): string {
  const p = PROVIDERS.find((x) => x.id === providerId);
  return p?.defaultModel ?? p?.defaultModels?.[0]?.id ?? '';
}

