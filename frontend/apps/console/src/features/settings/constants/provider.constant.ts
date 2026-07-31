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
  /** 推理生效時 API 是否拒絕自訂 temperature（OpenAI 為 true；Gemini/ByteDance 實測皆可自訂）。 */
  temperatureLockedWhenThinking?: boolean;
  /** 不論 thinking 狀態，API 一律**拒絕**自訂 temperature（只接受預設值）；與上者「僅推理生效時拒」
   * 為不同機制。⚠️ 2026-07-31 實測校正——原註解寫「伺服器靜默忽略」是錯的（ByteDance seed 系列送
   * 0.3 正常受理、送 99/-5 才被 InvalidParameter 拒 ⇒ 它真的會採用），該宣告已改為不鎖。 */
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

/** reasoning_effort 完整值域（跨三供應商聯集，非單一 model 的實際支援值）。
 * UI 的按鈕清單＝**provider 級 ∪ 該 model 能力表**再依本清單排序（見 LlmKnobs 的 REASONING_CHOICES）——
 * per-model 覆寫可能多出 provider 級沒有的檔位（如 gemini-2.5-flash 的 none），只取 provider 級會讓它
 * 點不到。個別 model 不支援的值用 disabled 灰掉、不從清單移除（避免版位在不同 model 間跳動）。
 * 資料源＝config/global/llm_model.json。 */
export const REASONING: string[] = llm.reasoning;

/** Model 下拉最低版本門檻（僅 gpt-* 受限）；動態 API 清單與 curated 顯示皆以此過濾。 */
export const MODEL_MIN_VERSION: string = llm.modelMinVersion;

/** LLM 消費功能區清單；資料源＝config/global/llm_model.json areas[]。 */
export const LLM_AREAS: string[] = llm.areas ?? ['prejudge', 'prompt_debug', 'sandbox'];

/** 功能區的顯示名（設定面板「這筆配置被誰用著」等處顯示用；未登記的區退回原始 key）。 */
export const LLM_AREA_LABELS: Record<string, string> = {
  prejudge: '初判分類',
  prompt_debug: 'Prompt 調試台',
  sandbox: 'Prompt 測試沙盒',
  prompt_revise: 'AI 定點改寫',
};

/** 各功能區「還沒選過配置」時的起點配置 id；資料源＝config/global/llm_model.json areaDefaults。
 *
 * ⚠️ **不是「使用者當前選了哪個」**——那份在 DB `settings.llm_area_configs`（team 共用單一份）；
 * 本常數只是「該區還沒綁過時」的起點。
 * 未登記的區回 undefined，`useLlmAreaConfig` 會退回清單第一筆。 */
export const LLM_AREA_DEFAULT_CONFIG_IDS: Record<string, string> = (llm.areaDefaults ??
  {}) as Record<string, string>;

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
const MODEL_CAPABILITY_OVERRIDES: Record<string, Partial<ModelCapability>> = (
  llm as { modelCapabilities?: Record<string, Partial<ModelCapability>> }
).modelCapabilities ?? {};

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
 * 切換供應商時用它決定 model，避免殘留另一供應商的 model id（見 settings.default_model_for 同規則）。
 * @param providerId 供應商 id（如 openai/gemini/bytedance）。
 */
export function defaultModelFor(providerId: string): string {
  const p = PROVIDERS.find((x) => x.id === providerId);
  return p?.defaultModel ?? p?.defaultModels?.[0]?.id ?? '';
}

/**
 * 由 model id 反推所屬供應商 id；查無回空字串（**不猜、不回退**——與後端
 * `settings.provider_id_for_model()` 同一份判準，供多模型跑批選擇器即時顯示「這個 model
 * 會打到哪個供應商」，讓使用者送出前就看得到，而不是等後端拒絕才知道）。
 * @param modelId LLM model id。
 */
export function providerIdForModel(modelId: string): string {
  return PROVIDERS.find((p) => (p.defaultModels ?? []).some((m) => m.id === modelId))?.id ?? '';
}
