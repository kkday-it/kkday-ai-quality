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
  return j<{ filename: string; sheets: SheetValidation[] }>(`${BASE}/inbound/validate`, {
    method: 'POST',
    body: fd,
  });
};

/** 上傳背景 job 單一工作表的進度（SSE 事件內 sheets[] 元素）。 */
export interface UploadSheetProgress {
  sheet_name: string;
  source: string;
  label: string;
  total: number;
  processed: number;
  inserted: number;
  failed: number;
  status: 'pending' | 'running' | 'done' | 'error';
  batch_id: string | null;
  errors: string[];
}

/** 上傳背景 job 進度快照（SSE `data` 事件 / /status 回傳）。 */
export interface UploadJobSnapshot {
  status: 'running' | 'done' | 'error';
  total_sheets: number;
  done_sheets: number;
  sheets: UploadSheetProgress[];
  invalid: { sheet_name: string; source: string; reason: string }[];
}

/**
 * 確認匯入（背景 job）：上傳勾選工作表 → 立即回 { job_id, sheets }；進度以 SSE 推送（見 uploadStreamUrl）。
 * @param file 同一份檔案（需重送供後端再解析）
 * @param selections 勾選清單 [{ sheet_name, source, note }]（note＝用戶備註，隨批次保存）
 * @returns { job_id, sheets: [{ sheet_name, source, label, total, valid, reason }] }
 */
export const uploadInbound = (
  file: File,
  selections: { sheet_name: string; source: string; note?: string }[],
): Promise<{
  job_id: string;
  sheets: { sheet_name: string; source: string; label: string; total: number; valid: boolean; reason: string }[];
}> => {
  const fd = new FormData();
  fd.append('file', file);
  fd.append('selections', JSON.stringify(selections));
  return j<{
    job_id: string;
    sheets: {
      sheet_name: string;
      source: string;
      label: string;
      total: number;
      valid: boolean;
      reason: string;
    }[];
  }>(`${BASE}/inbound/upload`, { method: 'POST', body: fd });
};

/**
 * 上傳進度 SSE 串流 URL（供原生 EventSource 直接連；免輪詢）。
 * @param jobId uploadInbound 回傳的 job_id
 */
export const uploadStreamUrl = (jobId: string): string =>
  `${BASE}/inbound/upload/stream?job_id=${encodeURIComponent(jobId)}`;

/** 上傳批次清單（新到舊；每列 batch_id/name/source/row_count/uploaded_at/original_name/note 等動態欄）。 */
export const getBatches = (): Promise<Record<string, unknown>[]> =>
  j<Record<string, unknown>[]>(`${BASE}/batches`);

/** 某批次的錄入明細（點擊批次展開用）。 */
export const getBatchItems = (batchId: string): Promise<Record<string, unknown>[]> =>
  j<Record<string, unknown>[]>(`${BASE}/batches/${encodeURIComponent(batchId)}/items`);

/** 批次 CSV 匯出 URL（給 window.open / a 連結直接下載）。 */
export const exportBatchUrl = (batchId: string) =>
  `${BASE}/batches/${encodeURIComponent(batchId)}/export`;
