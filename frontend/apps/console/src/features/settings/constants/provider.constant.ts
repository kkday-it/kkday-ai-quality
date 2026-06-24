// LLM 供應商定義與設定面板常數。

/** 開頁快取 key（排除 api_token） */
export const CACHE_KEY = 'aipq_settings_cache';
/** 每個 base_url::model 的旋鈕記憶 key */
export const OVERRIDES_KEY = 'aipq_model_overrides';
/** 各供應商自訂 model 列表 key（手動輸入累積） */
export const PROVIDER_MODELS_KEY = 'aipq_provider_models';

export interface Provider {
  id: string;
  label: string;
  base_url: string;
  api_token: string;
  defaultModels: string[];
  thinking?: string; // 'on' | 'off'
  reasoning_effort?: string;
}

/** 單一 model 的旋鈕覆寫記憶。 */
export interface ModelOverride {
  thinking: string;
  reasoning_effort: string;
  temperature: number | null;
}

/**
 * 供應商定義：選供應商一次帶入 base_url 與該供應商的 model 清單。
 * GPT model id 對齊 OpenAI 官方（gpt-5.5 / gpt-5.4 / gpt-5.4-mini / gpt-5.4-nano）。
 * 安全：api_token 一律留空、不寫死於原始碼（不入 git / 不進前端 bundle），由使用者於面板填入後存後端。
 */
export const PROVIDERS: Provider[] = [
  {
    id: 'openai',
    label: 'GPT (OpenAI)',
    base_url: 'https://api.openai.com/v1',
    api_token: '',
    defaultModels: ['gpt-5.5', 'gpt-5.4', 'gpt-5.4-mini', 'gpt-5.4-nano'],
    thinking: 'off',
    reasoning_effort: 'medium',
  },
  {
    id: 'gemini',
    label: 'Gemini (Google)',
    base_url: 'https://generativelanguage.googleapis.com/v1beta/openai',
    api_token: '',
    defaultModels: ['gemini-3.5-flash'],
  },
  {
    id: 'bytedance',
    label: '字节 ByteDance',
    base_url: 'https://ark.ap-southeast.bytepluses.com/api/v3',
    api_token: '',
    defaultModels: ['seed-2-0-lite-260228'],
  },
];

/** Reasoning effort 對齊 OpenAI 官方 GPT-5.4 支援值；預設 medium。 */
export const REASONING = ['none', 'low', 'medium', 'high', 'xhigh'];
