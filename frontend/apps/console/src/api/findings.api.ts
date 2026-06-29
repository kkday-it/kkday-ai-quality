// Findings 領域 API：查詢、狀態更新、判決鏈路。
import { BASE, JSON_HEADERS, j } from './http.api';

export const getFindings = (
  params: { prodOid?: string; dimension?: string; verdict?: string } = {},
) => {
  const q = new URLSearchParams();
  if (params.prodOid) q.set('prod_oid', params.prodOid);
  if (params.dimension) q.set('dimension', params.dimension);
  if (params.verdict) q.set('verdict', params.verdict);
  const s = q.toString();
  return j(`${BASE}/findings${s ? `?${s}` : ''}`);
};

export const patchStatus = (findingId: string, status: string) =>
  j(`${BASE}/findings/${encodeURIComponent(findingId)}/status`, {
    method: 'PATCH',
    headers: JSON_HEADERS,
    body: JSON.stringify({ status }),
  });

export const diagnose = (prodOid: string) =>
  j(`${BASE}/diagnose`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ prod_oid: prodOid }),
  });
