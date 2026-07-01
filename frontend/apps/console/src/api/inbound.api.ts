// 資料進線領域 API：乾跑校驗、確認上傳、批次管理、明細、匯出。
import { BASE, j } from './http.api';

/** /validate 回傳的每工作表校驗結果。 */
export interface SheetValidation {
  sheet_name: string;
  detected_source: string | null;
  label: string;
  status: 'ok' | 'fail' | 'unknown';
  missing_headers: string[];
  row_count: number;
  reason: string;
}

/**
 * 乾跑校驗（不落庫）：上傳檔案 → 逐工作表自動辨識來源 + 必備表頭校驗。
 * @param file 使用者選的檔案（CSV 單表 / xlsx 多分頁）
 * @returns { filename, sheets: SheetValidation[] }
 */
export const validateInbound = (file: File): Promise<{ filename: string; sheets: SheetValidation[] }> => {
  const fd = new FormData();
  fd.append('file', file);
  return j(`${BASE}/inbound/validate`, { method: 'POST', body: fd });
};

/**
 * 確認匯入：只匯入用戶勾選且校驗通過的工作表。
 * @param file 同一份檔案（需重送供後端再解析）
 * @param selections 勾選清單 [{ sheet_name, source }]
 * @returns { results: [{ sheet_name, source, label, batch_id, inserted, total } | { error }] }
 */
export const uploadInbound = (
  file: File,
  selections: { sheet_name: string; source: string }[],
) => {
  const fd = new FormData();
  fd.append('file', file);
  fd.append('selections', JSON.stringify(selections));
  return j(`${BASE}/inbound/upload`, { method: 'POST', body: fd });
};

/** 上傳批次清單（新到舊）。 */
export const getBatches = () => j(`${BASE}/batches`);

/** 某批次的錄入明細（點擊批次展開用）。 */
export const getBatchItems = (batchId: string) =>
  j(`${BASE}/batches/${encodeURIComponent(batchId)}/items`);

/** 批次 CSV 匯出 URL（給 window.open / a 連結直接下載）。 */
export const exportBatchUrl = (batchId: string) =>
  `${BASE}/batches/${encodeURIComponent(batchId)}/export`;
