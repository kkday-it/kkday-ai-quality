// 資料來源領域 API：來源連線測試等。
import { BASE, JSON_HEADERS, j } from './http.api';

/** 測試 QC DB（PostgreSQL）連線；空/遮罩密碼沿用後端既存明文值。回 `{ ok, error? }`。 */
export const testQcDb = (patch: Record<string, unknown>) =>
  j(`${BASE}/datasource/qc-db/test`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(patch),
  });
