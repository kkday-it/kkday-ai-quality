// 設定型別（對齊後端 user_settings.data 結構，A schema：連線層 + 功能區默認旋鈕層）。
// 機密（LLM token / QC password）不在連線本體：LLM token 存 llm_tokens（per-provider）、
// QC password 存 qc_passwords（per-env）。詳見 backend/app/core/settings.py。

/** 單一供應商連線（openai/gemini/bytedance 各一條）；token 另存 llm_tokens。 */
export interface LlmConnection {
  base_url: string;
}

/** LLM 消費功能區（三個前端旋鈕配置槽）。 */
export type LlmArea = 'prejudge' | 'prompt_debug' | 'sandbox';

/** thinking 旋鈕值域：default＝不送、用 API 預設；on/off＝顯式開關。 */
export type LlmThinking = 'default' | 'on' | 'off';
/** reasoning_effort 旋鈕值域（含 minimal，僅部分 model 支援，見 modelCapabilities）。 */
export type LlmReasoningEffort = 'default' | 'none' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh';

/** 單一功能區的旋鈕默認（team 共用）；provider 決定連線反查對象。 */
export interface LlmAreaDefault {
  provider: string;
  model: string;
  temperature: number | null; // null＝用 API 預設
  thinking: LlmThinking;
  reasoning_effort: LlmReasoningEffort;
}

/** 本次執行臨時旋鈕覆寫（不落庫）；provider 可切換本次用哪個供應商連線。 */
export interface LlmOverrides {
  provider?: string;
  model?: string;
  temperature?: number | null;
  thinking?: LlmThinking;
  reasoning_effort?: LlmReasoningEffort;
}

/** 單一環境 QC DB 連線（sit/stage/production 各一條）；password 另存 qc_passwords。 */
export interface QcConnection {
  host: string;
  port: number | null;
  user: string;
}

/** 後端 GET /api/settings(/raw) 回傳的設定全貌（raw 為明文機密、masked 為遮罩）。 */
export interface SettingsBundle {
  llm_connections?: Record<string, LlmConnection>;
  llm_tokens?: Record<string, string>; // { provider_id: token } per-provider
  llm_area_defaults?: Partial<Record<LlmArea, LlmAreaDefault>>;
  provider_models?: Record<string, string[]>;
  qc_connections?: Record<string, QcConnection>;
  qc_passwords?: Record<string, string>; // { env_id: password } per-env
  /** 導出偏好：Google Drive 上傳資料夾 URL（null/缺省＝未設，退全域 config 預設）。 */
  gdrive_upload_folder_url?: string | null;
  stub_mode?: boolean;
  has_token?: boolean;
  has_qc_db_password?: boolean;
  /** 逐供應商是否已配 token（連線卡個別顯示用）。 */
  provider_has_token?: Record<string, boolean>;
  /** 逐環境是否已配密碼（連線卡個別顯示用）。 */
  qc_env_has_password?: Record<string, boolean>;
}
