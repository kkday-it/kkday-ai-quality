// 判決規則管理 API（config/ai_judge/ 的 7 rule + schema 的版本化）。
// 後端 /api/judge-rules：檔案＝默認 seed、DB＝live+歷史；存檔前 jsonschema 驗證，非法回 422。
import { BASE, JSON_HEADERS, getToken, j } from './http.api';

/** rule code：'C-1'..'C-7' | 'schema'。 */
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
export const listRules = (): Promise<RuleMeta[]> => j(`${BASE}/judge-rules`);

/** 讀某 rule active content（或指定 version）。 */
export const getRule = (code: RuleCode, version?: number): Promise<RuleContentResp> =>
  j(`${BASE}/judge-rules/${encodeURIComponent(code)}${version ? `?version=${version}` : ''}`);

/** 某 rule 全版本清單（新到舊）。 */
export const getRuleHistory = (code: RuleCode): Promise<RuleVersionMeta[]> =>
  j(`${BASE}/judge-rules/${encodeURIComponent(code)}/history`);

/** 取特定版本完整 content（diff / 恢復預覽用）。 */
export const getRuleVersion = (code: RuleCode, version: number): Promise<RuleContentResp> =>
  j(`${BASE}/judge-rules/${encodeURIComponent(code)}/versions/${version}`);

/** 存檔（後端先 jsonschema 驗證 → 新 active 版）。 */
export const saveRule = (
  code: RuleCode,
  content: unknown,
  note = '',
): Promise<RuleSaveResult> =>
  j(`${BASE}/judge-rules/${encodeURIComponent(code)}`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ content, note }),
  });

/** 恢復歷史版本（複製為新 active 版）。 */
export const restoreRule = (code: RuleCode, version: number): Promise<RuleSaveResult> =>
  j(`${BASE}/judge-rules/${encodeURIComponent(code)}/restore/${version}`, {
    method: 'POST',
    headers: JSON_HEADERS,
  });

/** 恢復默認（讀 config/ai_judge/ 檔內容存為新 active 版）。 */
export const resetRuleDefault = (code: RuleCode): Promise<RuleSaveResult> =>
  j(`${BASE}/judge-rules/${encodeURIComponent(code)}/reset-default`, {
    method: 'POST',
    headers: JSON_HEADERS,
  });

/** 恢復所有歸因分類（C-N，排除 schema）為檔案默認，各新增一版覆蓋當前；skipped＝無默認檔跳過的 code。 */
export const resetAllRuleDefaults = (): Promise<{ reset: RuleSaveResult[]; skipped: string[] }> =>
  j(`${BASE}/judge-rules/reset-default-all`, {
    method: 'POST',
    headers: JSON_HEADERS,
  });

/**
 * 導出全部判決規則為 Excel（DB active 版本）：C-N 歸因分類各一分頁＋ global 判決總規範，
 * 格式對齊 data/問題分類層級結構.xlsx。回 Blob 供前端下載（非 JSON，故不走 j 包裝）。
 * @throws {Error} 非 2xx 時拋出 `導出失敗 ${status}`。
 */
export const exportRulesXlsx = async (): Promise<Blob> => {
  const token = getToken();
  const res = await fetch(`${BASE}/judge-rules/export.xlsx`, {
    headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}) },
  });
  if (!res.ok) throw new Error(`導出失敗 ${res.status}`);
  return res.blob();
};
