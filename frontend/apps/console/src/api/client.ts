// 打後端 FastAPI（dev 經 vite proxy /api → :8100）
const BASE = '/api';

async function j(url: string, init?: RequestInit) {
  const r = await fetch(url, init);
  if (!r.ok) throw new Error(`${r.status} ${url}`);
  return r.json();
}

export const getAggregate = () => j(`${BASE}/findings/aggregate`);

export const getFindings = (prodOid?: string) =>
  j(`${BASE}/findings${prodOid ? `?prod_oid=${encodeURIComponent(prodOid)}` : ''}`);

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
