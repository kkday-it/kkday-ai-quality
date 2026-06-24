// 資料進線領域 API：上傳、批次管理、明細、匯出。
import { BASE, j } from './http.api';

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
