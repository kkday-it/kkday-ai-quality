// LLM 供應商定義與設定面板常數。
// 供應商目錄 / reasoning 選項的「資料」來自 repo 根 config/defaults.json（跨語言共用單一真相源）；
// 本檔僅保留型別與前端衍生預設，不再寫死 base_url / model 清單。

import defaults from '@config/defaults.json';

/** 下拉一個 model 選項：id + 質性描述（成本/用途 hint，內聚於各 model，不另立 modelMeta map）。 */
export interface ModelOption {
  id: string;
  desc?: string;
}

export interface Provider {
  id: string;
  label: string;
  base_url: string;
  /** 一律留空、不寫死於原始碼（不入 git / 不進 bundle），由使用者於面板填入後存後端。 */
  api_token?: string;
  /** 預設選中的 model id（與 defaultModels 排序解耦；缺省則回退 defaultModels[0].id）。 */
  defaultModel?: string;
  /** model 下拉清單；{ id, desc } 物件、排序由強到弱（能力高者在前）。 */
  defaultModels: ModelOption[];
  thinking?: string; // 'on' | 'off'
  reasoning_effort?: string;
}


/**
 * 供應商定義：選供應商一次帶入 base_url 與該供應商的 model 清單。
 * 資料源＝config/defaults.json（GPT model id 對齊 OpenAI 官方 gpt-5.5 / gpt-5.4 / mini / nano）。
 * JSON 字面型別與 Provider[] 結構相容（api_token 選填、不在 JSON 中），以 cast 收斂。
 */
export const PROVIDERS = defaults.llm.providers as Provider[];

/** Reasoning effort 對齊 OpenAI 官方支援值；資料源＝config/defaults.json。 */
export const REASONING: string[] = defaults.llm.reasoning;

/** Model 下拉最低版本門檻（僅 gpt-* 受限）；動態 API 清單與 curated 顯示皆以此過濾。 */
export const MODEL_MIN_VERSION: string = defaults.llm.modelMinVersion;

/** openai 為預設供應商；其 preset 作為 LLM 設定表單的初始展示預設（與後端「未設定即用 API 預設」語義不同，故獨立）。 */
const openai = PROVIDERS.find((p) => p.id === 'openai');

/**
 * LLM 設定面板表單的「UI 展示預設」。衍生自 openai 供應商 preset，避免與 config/defaults.json 重複寫死。
 * 注意：這是「前端開頁顯示值」，非後端 _DEFAULT（後端 thinking/effort 用 'default' 表示「不送、用 API 預設」）。
 */
export const DEFAULT_LLM_FORM = {
  model: openai?.defaultModel ?? openai?.defaultModels[0]?.id ?? 'gpt-5.4-mini',
  base_url: '',
  temperature: 0 as number,
  thinking: openai?.thinking ?? 'off',
  reasoning_effort: openai?.reasoning_effort ?? 'medium',
};
