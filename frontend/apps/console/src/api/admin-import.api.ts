// 全庫資料包領域 API：導出（背景 job）、乾跑校驗、確認匯入、進度 SSE。
// 對應後端 app/api/routers/admin_import.py（安全匯入：只灌白名單表、不執行 SQL）。
// 導出走通用 export_jobs：本檔只起 job，進度/下載沿用 @/api 的 exports.api（exportStreamUrl/downloadExport）。
import { BASE, j } from './http.api';

/** /validate 回傳的每表匯入計畫。 */
export interface TableImportPlan {
  name: string;
  in_pack: boolean;
  sensitive: boolean;
  pack_rows: number;
  db_rows: number;
  will_truncate: boolean;
  will_insert: boolean;
  unknown_columns: string[];
}

/** 乾跑校驗回傳：schema 檢查 + 每表計畫 + 需輸入的確認短語。 */
export interface ValidateReport {
  ok: boolean;
  schema_ok: boolean;
  manifest_head: string | null;
  current_head: string | null;
  generated_at: string | null;
  sensitive_present: boolean;
  confirm_phrase: string;
  tables: TableImportPlan[];
  errors: string[];
  warnings: string[];
}

/** 匯入背景 job 進度快照（SSE `data` 事件）。 */
export interface ImportJobSnapshot {
  status: 'running' | 'done' | 'error';
  current_table: string;
  done_tables: number;
  total_tables: number;
  inserted: Record<string, number>;
  error: string;
}

/**
 * 啟動全庫資料包導出背景 job（逐表回報進度）→ 回 { job_id }。
 * 進度追蹤與下載沿用通用 export_jobs：以 useExportJob(run) 傳入本函式即可。
 * @param includeSensitive 是否併入敏感表（users/user_settings，含機密）
 */
export const startDatapackExport = (includeSensitive = false): Promise<{ job_id: string }> =>
  j<{ job_id: string }>(`${BASE}/admin/export/start?include_sensitive=${includeSensitive}`, {
    method: 'POST',
  });

/**
 * 乾跑校驗資料包（不落庫）：上傳 zip → 回 schema 檢查與每表匯入計畫。
 * @param file 資料包 zip（由 scripts/tools/dump_datapack.py 產生）
 * @param includeSensitive 是否納入敏感表（users/user_settings）
 */
export const validateDatapack = (file: File, includeSensitive = false): Promise<ValidateReport> => {
  const fd = new FormData();
  fd.append('file', file);
  fd.append('include_sensitive', String(includeSensitive));
  return j<ValidateReport>(`${BASE}/admin/import/validate`, { method: 'POST', body: fd });
};

/**
 * 確認匯入（背景 job）：核對確認短語後啟動單交易 truncate-then-load。
 * @param file 資料包 zip
 * @param confirmPhrase 使用者輸入的確認短語（須等於 ValidateReport.confirm_phrase）
 * @param includeSensitive 是否納入敏感表
 * @returns { job_id }（據此連 SSE 觀察進度）
 */
export const importDatapack = (
  file: File,
  confirmPhrase: string,
  includeSensitive = false,
): Promise<{ job_id: string }> => {
  const fd = new FormData();
  fd.append('file', file);
  fd.append('confirm_phrase', confirmPhrase);
  fd.append('include_sensitive', String(includeSensitive));
  return j<{ job_id: string }>(`${BASE}/admin/import`, { method: 'POST', body: fd });
};

/**
 * 匯入進度 SSE 連線 URL（前端以原生 EventSource 消費，比照 inbound 慣例）。
 * @param jobId importDatapack 回傳的 job_id
 */
export const importStreamUrl = (jobId: string): string =>
  `${BASE}/admin/import/stream?job_id=${encodeURIComponent(jobId)}`;
