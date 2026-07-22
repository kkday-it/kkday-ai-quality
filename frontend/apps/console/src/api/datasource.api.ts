// 資料來源領域 API：來源連線測試等。
import { BASE, JSON_HEADERS, j } from './http.api';

/** 後端 /datasource/qc-db/test 回傳形狀：純連通性檢查結果。 */
export interface QcDbTestResult {
  ok: boolean;
  error?: string;
}

/**
 * 測試 QC DB（PostgreSQL）連線是否可連通（bootstrap 庫 `SELECT 1`）。
 * 空/遮罩密碼沿用後端既存明文值。
 * @param patch 當前表單值（qc_db_env / host / port / user / password…）
 */
export const testQcDb = (patch: Record<string, unknown>): Promise<QcDbTestResult> =>
  j<QcDbTestResult>(`${BASE}/datasource/qc-db/test`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(patch),
  });
