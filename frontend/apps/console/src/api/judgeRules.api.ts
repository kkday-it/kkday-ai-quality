// 初判規則管理 API（RULE_CODES：product_vertical + source_mapping + prompt_* 的版本化）。
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

/** 列所有初判規則 active 版 meta。 */
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

/** 恢復全部規則（source_mapping + 7 支初判 Prompt，排除 product_vertical）為檔案默認，各新增一版覆蓋當前；skipped＝無默認檔跳過的 code。 */
export const resetAllRuleDefaults = (): Promise<{ reset: RuleSaveResult[]; skipped: string[] }> =>
  j<{ reset: RuleSaveResult[]; skipped: string[] }>(`${BASE}/judge-rules/reset-default-all`, {
    method: 'POST',
    headers: JSON_HEADERS,
  });

/**
 * 啟動初判 Prompt 包導出背景 job → {job_id, filename}（立即回）。
 * 打包 prompts 目錄（Prompt-as-Source 唯一真相源：7 支 prompt md ＋ 引擎契約 README ＋ 基線 BASELINE）為 zip。
 * 進度走 /api/exports SSE（見 exports.api），完成後 downloadExport(job_id) 取檔。
 */
export const startRulesExport = (): Promise<{ job_id: string; filename: string }> =>
  j<{ job_id: string; filename: string }>(`${BASE}/judge-rules/export`, {
    method: 'POST',
    headers: JSON_HEADERS,
  });

// ── 初判 Prompt 草稿（prompt_* 每 rule_code 一份共享草稿；未入庫的編輯中內容）──
// 草稿與版本表分離：存草稿不影響初判；沙盒可直接送測（雙跑對比），滿意後 saveRule 入庫成新 active 版。

/** 草稿完整內容（GET 單筆）。 */
export interface PromptDraft {
  /** {_meta, text}（同 rule content 格式）。 */
  content: Record<string, unknown>;
  /** 從哪個版本分叉（stale 偵測：< 現行 active 版本號時提示「active 已前進」）。 */
  base_version: number;
  updated_by: string | null;
  updated_at: string | null;
}

/** 草稿存在狀態（列表用，不含 content）。 */
export interface PromptDraftMeta {
  rule_code: RuleCode;
  base_version: number;
  updated_by: string | null;
  updated_at: string | null;
}

/** 取某 prompt 的草稿；無草稿回 draft: null（200，非錯誤）。 */
export const getRuleDraft = (
  code: RuleCode,
): Promise<{ rule_code: RuleCode; draft: PromptDraft | null }> =>
  j<{ rule_code: RuleCode; draft: PromptDraft | null }>(
    `${BASE}/judge-rules/${encodeURIComponent(code)}/draft`,
  );

/** 列所有存在草稿的 prompt（供沙盒版本選擇器一次拉取草稿存在狀態）。 */
export const listRuleDrafts = (): Promise<PromptDraftMeta[]> =>
  j<PromptDraftMeta[]>(`${BASE}/judge-rules/drafts`);

/** 寫入/覆蓋草稿（last-write-wins；存檔寬鬆只驗 text 非空，送測/入庫才強驗）。 */
export const saveRuleDraft = (
  code: RuleCode,
  content: Record<string, unknown>,
  baseVersion: number,
): Promise<{ rule_code: RuleCode; saved: boolean }> =>
  j<{ rule_code: RuleCode; saved: boolean }>(
    `${BASE}/judge-rules/${encodeURIComponent(code)}/draft`,
    {
      method: 'PUT',
      headers: JSON_HEADERS,
      body: JSON.stringify({ content, base_version: baseVersion }),
    },
  );

/** 刪除草稿（入庫採納後清理／手動捨棄）；deleted=false＝原本就無草稿（冪等）。 */
export const deleteRuleDraft = (
  code: RuleCode,
): Promise<{ rule_code: RuleCode; deleted: boolean }> =>
  j<{ rule_code: RuleCode; deleted: boolean }>(
    `${BASE}/judge-rules/${encodeURIComponent(code)}/draft`,
    { method: 'DELETE', headers: JSON_HEADERS },
  );

/** dry-run 驗證 prompt md 全文（不落庫）：三節/Schema/{TEXT}/{POLARITY}/Taxonomy；
 * 內容不合法回 {valid:false, error}（200，非 HTTP 錯誤）。 */
export const validateRuleText = (
  code: RuleCode,
  text: string,
): Promise<{ valid: boolean; error?: string }> =>
  j<{ valid: boolean; error?: string }>(
    `${BASE}/judge-rules/${encodeURIComponent(code)}/validate`,
    { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ text }) },
  );
