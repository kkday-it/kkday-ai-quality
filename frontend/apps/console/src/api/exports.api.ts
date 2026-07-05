// 通用導出 job 客戶端：進度串流 URL / 停止 / 取檔（跨領域共用，對齊 backend routers/exports.py）。
// 各領域自有 start 端點（startProblemsExport / startRulesExport）回 job_id，其餘生命週期都走這三支。
import { BASE, getToken, j } from './http.api';

/** 導出 job 進度快照（SSE 每 ~0.5s 推一次；對齊 export_jobs._new_snapshot）。 */
export interface ExportJobSnapshot {
  /** 狀態機：running → done｜running → cancelling → cancelled｜error。 */
  status: string;
  /** 總量（0＝builder 尚未算出，前端顯示「準備中」）。 */
  total: number;
  /** 已完成量（review 數 / 分頁數，依 builder 而定）。 */
  processed: number;
  /** 建議下載檔名（前端多以本地時間戳自訂，故通常忽略）。 */
  filename: string;
  /** error 狀態時的訊息。 */
  error: string;
}

/**
 * 導出進度 SSE 串流 URL（供原生 EventSource 直連）。
 * @param jobId startXxxExport 回傳的 job_id（capability token，端點免 auth header）
 */
export const exportStreamUrl = (jobId: string): string =>
  `${BASE}/exports/stream?job_id=${encodeURIComponent(jobId)}`;

/** 停止導出 job（builder 下個 check 點收斂為 cancelled，不產出檔案）→ 更新後快照。 */
export const cancelExport = (jobId: string) =>
  j(`${BASE}/exports/cancel?job_id=${encodeURIComponent(jobId)}`, { method: 'POST' });

/**
 * 取回已完成導出 job 的 xlsx blob（attachment）；一次性，後端取後即清 job 與結果。
 * @throws {Error} 非 2xx 時拋出 `下載失敗 ${status}`。
 */
export const downloadExport = async (jobId: string): Promise<Blob> => {
  const token = getToken();
  const res = await fetch(`${BASE}/exports/download?job_id=${encodeURIComponent(jobId)}`, {
    headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}) },
  });
  if (!res.ok) throw new Error(`下載失敗 ${res.status}`);
  return res.blob();
};
