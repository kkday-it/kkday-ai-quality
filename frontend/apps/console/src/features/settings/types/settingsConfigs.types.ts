// 多套設定 config 型別（對齊後端 user_settings.data 結構）。
// 機密（LLM token / QC password）不在 config 本體：token 跨 config 共用 provider_tokens（per-provider）、
// QC password 存 qc_passwords（per-config_id）。詳見 backend/app/core/settings.py。

/** 單套 LLM 模型 config。 */
export interface LlmConfig {
  id: string;
  label: string;
  provider: string; // openai | gemini | bytedance | custom（顯示用；token 實以 base_url 反推 provider 為 key）
  base_url: string;
  model: string;
  temperature: number | null; // null＝用 API 預設
  thinking: string; // default | on | off
  reasoning_effort: string; // default | none | low | medium | high | xhigh
}

/** 單套 QC DB（PostgreSQL）連線 config。 */
export interface QcConfig {
  id: string;
  label: string;
  env: string; // sit | stage
  host: string;
  port: number | null;
  user: string;
  names: string[]; // 多選 database
  schemas: string[]; // 多選 schema
}

/** 後端 GET /api/settings(/raw) 回傳的設定全貌（raw 為明文機密、masked 為遮罩）。 */
export interface SettingsBundle {
  llm_configs?: LlmConfig[];
  active_llm_config_id?: string | null;
  provider_tokens?: Record<string, string>;
  provider_models?: Record<string, string[]>;
  qc_configs?: QcConfig[];
  active_qc_config_id?: string | null;
  qc_passwords?: Record<string, string>;
  stub_mode?: boolean;
  has_token?: boolean;
  has_qc_db_password?: boolean;
}
