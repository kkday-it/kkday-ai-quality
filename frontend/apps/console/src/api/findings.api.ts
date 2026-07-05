// Findings 領域 API：查詢、狀態更新、判決鏈路。
import { BASE, JSON_HEADERS, j } from './http.api';

export const getFindings = (
  params: { prodOid?: string; dimension?: string } = {},
): Promise<Record<string, unknown>[]> => {
  const q = new URLSearchParams();
  if (params.prodOid) q.set('prod_oid', params.prodOid);
  if (params.dimension) q.set('dimension', params.dimension);
  const s = q.toString();
  return j<Record<string, unknown>[]>(`${BASE}/findings${s ? `?${s}` : ''}`);
};

export const patchStatus = (findingId: string, status: string) =>
  j(`${BASE}/findings/${encodeURIComponent(findingId)}/status`, {
    method: 'PATCH',
    headers: JSON_HEADERS,
    body: JSON.stringify({ status }),
  });

/** 歸因分類級聯節點（巢狀）：value＝L1 域 code 或 L2/L3 的 C-code。 */
export interface CascadeNode {
  value: string;
  label: string;
  children?: CascadeNode[];
}

/** 取歸因分類級聯樹（L1→L2→L3）供標真值 a-cascader 選項。 */
export const getTaxonomyCascade = (): Promise<CascadeNode[]> =>
  j<CascadeNode[]>(`${BASE}/findings/taxonomy-cascade`);

/** 標真值把關評分結果：LLM 對提議真值的信心 + 與原判對比 + 是否需填理由。 */
export interface TrueLabelEval {
  finding_id: string;
  proposed_label: string;
  llm_confidence: number;
  original_confidence: number | null;
  delta: number | null;
  reason_llm: string;
  reason_required: boolean;
  threshold: number;
}

/** LLM 對「提議真值 vs 反饋原文」重判評分（標真值確認時跑）；回信心對比 + 是否需填理由。 */
export const evaluateTrueLabel = (findingId: string, proposedLabel: string): Promise<TrueLabelEval> =>
  j<TrueLabelEval>(`${BASE}/findings/${encodeURIComponent(findingId)}/true_label/evaluate`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ proposed_label: proposedLabel }),
  });

/**
 * 人工標註單筆歸因真值分類 true_label（null/空＝清除）；重判依 finding_id 保留。
 * reason＝LLM 信心明顯下降時的修改理由；llmConf＝標註當下 LLM 對真值的契合信心（audit + 後端把關）。
 */
export const updateTrueLabel = (
  findingId: string,
  trueLabel: string | null,
  opts: { reason?: string; llmConf?: number } = {},
) =>
  j(`${BASE}/findings/${encodeURIComponent(findingId)}/true_label`, {
    method: 'PATCH',
    headers: JSON_HEADERS,
    body: JSON.stringify({ true_label: trueLabel, reason: opts.reason, llm_conf: opts.llmConf }),
  });

export const diagnose = (prodOid: string) =>
  j(`${BASE}/diagnose`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ prod_oid: prodOid }),
  });
