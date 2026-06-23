// 打後端 FastAPI（dev 經 vite proxy /api → :8100）
const BASE = '/api';

async function j(url: string, init?: RequestInit) {
  const r = await fetch(url, init);
  if (!r.ok) throw new Error(`${r.status} ${url}`);
  return r.json();
}

export const getAggregate = () => j(`${BASE}/findings/aggregate`);

export const getFindings = (params: { prodOid?: string; dimension?: string; verdict?: string } = {}) => {
  const q = new URLSearchParams();
  if (params.prodOid) q.set('prod_oid', params.prodOid);
  if (params.dimension) q.set('dimension', params.dimension);
  if (params.verdict) q.set('verdict', params.verdict);
  const s = q.toString();
  return j(`${BASE}/findings${s ? `?${s}` : ''}`);
};

export const getProducts = () => j(`${BASE}/products`);

export const diagnose = (prodOid: string) =>
  j(`${BASE}/diagnose`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prod_oid: prodOid }),
  });

export const patchStatus = (findingId: string, status: string) =>
  j(`${BASE}/findings/${encodeURIComponent(findingId)}/status`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status }),
  });
