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

/**
 * 批量上傳資料檔（CSV/Excel）→ 後端解析錄入。
 * @param file 使用者選的檔案
 * @param source 來源標記（如 presale_postsale 售前售後進線 / review 評論）
 * @returns 後端回傳 { inserted, total, source, preview }
 */
export const uploadInbound = (file: File, source = 'csv') => {
  const fd = new FormData();
  fd.append('file', file);
  fd.append('source', source);
  // FormData 不可手動設 Content-Type，瀏覽器自動帶 multipart boundary
  return j(`${BASE}/inbound/upload`, { method: 'POST', body: fd });
};

/** 列出已錄入標的（可依 status 過濾），新到舊。 */
export const getInbound = (status?: string) =>
  j(`${BASE}/inbound${status ? `?status=${encodeURIComponent(status)}` : ''}`);

/** 上傳批次清單（新到舊）。 */
export const getBatches = () => j(`${BASE}/batches`);

/** 某批次的錄入明細（點擊批次展開用）。 */
export const getBatchItems = (batchId: string) =>
  j(`${BASE}/batches/${encodeURIComponent(batchId)}/items`);

/** 批次 CSV 匯出 URL（給 window.open / a 連結直接下載）。 */
export const exportBatchUrl = (batchId: string) =>
  `${BASE}/batches/${encodeURIComponent(batchId)}/export`;

/** 售前售後進線判定鏈路（第一階段主力管道）。source=fixture(MVP)|live(BQ)。 */
export const diagnosePresalePostsale = (source = 'fixture') =>
  j(`${BASE}/diagnose/presale-postsale`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source }),
  });

/** 讀 LLM 模型配置（api_token 已遮罩，附 has_token / stub_mode）。 */
export const getSettings = () => j(`${BASE}/settings`);

/** 儲存 LLM 模型配置（空/遮罩 token 不覆蓋既有）。 */
export const saveSettings = (patch: Record<string, unknown>) =>
  j(`${BASE}/settings`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
