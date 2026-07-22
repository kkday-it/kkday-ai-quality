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
  thinking?: string; // 'on' | 'off'
  reasoning_effort?: string;
  /** 該供應商是否支援 thinking 開關（provider 級能力預設；modelCapabilities 可對個別 model 覆寫）。 */
  supportsThinking?: boolean;
  /** 該供應商可用的 reasoning_effort 值域（取代舊固定全域 REASONING）。 */
  reasoningEffortOptions?: string[];
  /** 思考模式開啟時是否鎖定 temperature（OpenAI reasoning model 為 true；Gemini/ByteDance 為 false）。 */
  temperatureLockedWhenThinking?: boolean;
  /** 鎖定時實際送出的 temperature 值（通常 1）。 */
  lockedTemperatureValue?: number;
}

/**
 * 供應商定義：選供應商一次帶入 base_url 與該供應商的 model 清單。
 * 資料源＝config/global/llm_model.json（GPT model id 對齊 OpenAI 官方 gpt-5.5 / gpt-5.4 / mini / nano）。
 * JSON 字面型別與 Provider[] 結構相容（api_token 選填、不在 JSON 中），以 cast 收斂。
 */
export const PROVIDERS = llm.providers as Provider[];

/** Reasoning effort 對齊 OpenAI 官方支援值（全域值域回退）；資料源＝config/global/llm_model.json。 */
export const REASONING: string[] = llm.reasoning;

/** Model 下拉最低版本門檻（僅 gpt-* 受限）；動態 API 清單與 curated 顯示皆以此過濾。 */
export const MODEL_MIN_VERSION: string = llm.modelMinVersion;

/** LLM 消費功能區清單（prejudge/prompt_debug/sandbox）；資料源＝config/global/llm_model.json areas[]。 */
export const LLM_AREAS: string[] = llm.areas ?? ['prejudge', 'prompt_debug', 'sandbox'];

/** 每 model 可配參數能力（thinking 支援 / reasoning_effort 值域 / temperature 鎖定規則）。 */
export interface ModelCapability {
  supportsThinking: boolean;
  reasoningEffortOptions: string[];
  temperatureLockedWhenThinking: boolean;
  lockedTemperatureValue: number;
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
    reasoningEffortOptions: owner?.reasoningEffortOptions ?? REASONING,
    temperatureLockedWhenThinking: owner?.temperatureLockedWhenThinking ?? false,
    lockedTemperatureValue: owner?.lockedTemperatureValue ?? 1,
  };
  return { ...base, ...MODEL_CAPABILITY_OVERRIDES[modelId] };
}

