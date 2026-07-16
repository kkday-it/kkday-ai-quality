// 判決規則管理 API（RULE_CODES：product_vertical + source_mapping + prompt_* 的版本化）。
// 後端 /api/judge-rules：檔案＝默認 seed、DB＝live+歷史；存檔前依 code 型別驗證，非法回 422；存檔後熱重載。
// 本 API 管理範圍：product_vertical + source_mapping + prompt_polarity/prompt_C-1~6（版本化）；
// 判準 prompt 描述、極性閘門與證據政策為 judgment.json 靜態設定（改值需重啟後端），不在此 API 範圍。
import { BASE, JSON_HEADERS, j } from './http.api';

/** rule code：'product_vertical' | 'source_mapping' | 'prompt_polarity' | 'prompt_C-1'..'prompt_C-6'。 */
export type RuleCode = string;

/** 某 rule 的 active 版 meta（清單用）。 */
export interface RuleMeta {
  rule_code: RuleCode;
  version: number;
  author: string | null;
  note: string | null;
  created_at: string | null;
  /** L1 域中文名，自 content._meta.label（SSOT）；schema 等無此欄者為 null，由前端 fallback 補。 */
  label: string | null;
}

/** 歷史版本列。 */
export interface RuleVersionMeta {
  version: number;
  author: string | null;
  note: string | null;
  is_active: boolean;
  created_at: string | null;
}

/** 讀單一 rule（active 或特定版）。 */
export interface RuleContentResp {
  rule_code: RuleCode;
  version: number | null;
  content: Record<string, unknown>;
}

/** 存檔 / 恢復回傳。 */
export interface RuleSaveResult {
  rule_code: RuleCode;
  version: number;
}

/** 列所有判決規則 active 版 meta。 */
export const listRules = (): Promise<RuleMeta[]> => j<RuleMeta[]>(`${BASE}/judge-rules`);

/** 讀某 rule active content（或指定 version）。 */
export const getRule = (code: RuleCode, version?: number): Promise<RuleContentResp> =>
  j<RuleContentResp>(
    `${BASE}/judge-rules/${encodeURIComponent(code)}${version ? `?version=${version}` : ''}`,
  );

/** 某 rule 全版本清單（新到舊）。 */
export const getRuleHistory = (code: RuleCode): Promise<RuleVersionMeta[]> =>
  j<RuleVersionMeta[]>(`${BASE}/judge-rules/${encodeURIComponent(code)}/history`);

/** 取特定版本完整 content（diff / 恢復預覽用）。 */
export const getRuleVersion = (code: RuleCode, version: number): Promise<RuleContentResp> =>
  j<RuleContentResp>(`${BASE}/judge-rules/${encodeURIComponent(code)}/versions/${version}`);

/** 存檔（後端先 jsonschema 驗證 → 新 active 版）。 */
export const saveRule = (code: RuleCode, content: unknown, note = ''): Promise<RuleSaveResult> =>
  j<RuleSaveResult>(`${BASE}/judge-rules/${encodeURIComponent(code)}`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ content, note }),
  });

/** 恢復歷史版本（複製為新 active 版）。 */
export const restoreRule = (code: RuleCode, version: number): Promise<RuleSaveResult> =>
  j<RuleSaveResult>(`${BASE}/judge-rules/${encodeURIComponent(code)}/restore/${version}`, {
    method: 'POST',
    headers: JSON_HEADERS,
  });

/** 恢復默認（讀 config/ai_judge/ 檔內容存為新 active 版）。 */
export const resetRuleDefault = (code: RuleCode): Promise<RuleSaveResult> =>
  j<RuleSaveResult>(`${BASE}/judge-rules/${encodeURIComponent(code)}/reset-default`, {
    method: 'POST',
    headers: JSON_HEADERS,
  });

/** 恢復整體配置（source_mapping）為檔案默認，各新增一版覆蓋當前；skipped＝無默認檔跳過的 code。 */
export const resetAllRuleDefaults = (): Promise<{ reset: RuleSaveResult[]; skipped: string[] }> =>
  j<{ reset: RuleSaveResult[]; skipped: string[] }>(`${BASE}/judge-rules/reset-default-all`, {
    method: 'POST',
    headers: JSON_HEADERS,
  });

/**
 * 啟動判決 Prompt 包導出背景 job → {job_id, filename}（立即回）。
 * 打包 prompts 目錄（Prompt-as-Source 唯一真相源：7 支 prompt md ＋ 引擎契約 README ＋ 基線 BASELINE）為 zip。
 * 進度走 /api/exports SSE（見 exports.api），完成後 downloadExport(job_id) 取檔。
 */
export const startRulesExport = (): Promise<{ job_id: string; filename: string }> =>
  j<{ job_id: string; filename: string }>(`${BASE}/judge-rules/export`, {
    method: 'POST',
    headers: JSON_HEADERS,
  });
