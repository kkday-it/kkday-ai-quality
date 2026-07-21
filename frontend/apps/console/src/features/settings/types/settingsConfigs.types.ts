// 多套設定 config 型別（對齊後端 user_settings.data 結構）。
// 機密（LLM token / QC password）不在 config 本體：LLM token 存 llm_tokens（per-config_id，每套獨立）、
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
  env: string; // sit | stage | production
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
  llm_tokens?: Record<string, string>; // { config_id: token } per-config（每套配置各自獨立）
  provider_tokens?: Record<string, string>; // 舊 per-provider 共用（保留相容）
  provider_models?: Record<string, string[]>;
  qc_configs?: QcConfig[];
  active_qc_config_id?: string | null;
  qc_passwords?: Record<string, string>;
  /** 導出偏好：Google Drive 上傳資料夾 URL（null/缺省＝未設，退全域 config 預設）。 */
  gdrive_upload_folder_url?: string | null;
  stub_mode?: boolean;
  has_token?: boolean;
  has_qc_db_password?: boolean;
}
