// Findings 領域 API：查詢、狀態更新、判決鏈路。
import { BASE, JSON_HEADERS, j } from './http.api';

export const getFindings = (params: { prodOid?: string; dimension?: string } = {}) => {
  const q = new URLSearchParams();
  if (params.prodOid) q.set('prod_oid', params.prodOid);
  if (params.dimension) q.set('dimension', params.dimension);
  const s = q.toString();
  return j(`${BASE}/findings${s ? `?${s}` : ''}`);
};

export const patchStatus = (findingId: string, status: string) =>
  j(`${BASE}/findings/${encodeURIComponent(findingId)}/status`, {
    method: 'PATCH',
    headers: JSON_HEADERS,
    body: JSON.stringify({ status }),
  });

/** 人工標註單筆歸因真值分類 true_label（null/空＝清除）；重判依 finding_id 保留。 */
export const updateTrueLabel = (findingId: string, trueLabel: string | null) =>
  j(`${BASE}/findings/${encodeURIComponent(findingId)}/true_label`, {
    method: 'PATCH',
    headers: JSON_HEADERS,
    body: JSON.stringify({ true_label: trueLabel }),
  });

export const diagnose = (prodOid: string) =>
  j(`${BASE}/diagnose`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ prod_oid: prodOid }),
  });
