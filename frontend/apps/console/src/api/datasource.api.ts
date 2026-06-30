// 資料來源領域 API：來源連線測試等。
import { BASE, JSON_HEADERS, j } from './http.api';

/** 後端 /datasource/qc-db/test 回傳形狀：連線成功時附可用 database 清單供前端多選載入。 */
export interface QcDbTestResult {
  ok: boolean;
  /** 連線成功時的 pg_database 清單（排除 template / 不可連庫）。 */
  databases?: string[];
  /** 連線成功時起手庫的使用者 schema 清單（排除系統 schema）。 */
  schemas?: string[];
  error?: string;
}

/**
 * 測試 QC DB（PostgreSQL）連線並列舉可用 database。
 * 空/遮罩密碼沿用後端既存明文值；連線成功回傳 `databases` 供 Database 多選下拉動態載入。
 * @param patch 當前表單值（qc_db_env / host / port / names / user / password…）
 */
export const testQcDb = (patch: Record<string, unknown>): Promise<QcDbTestResult> =>
  j(`${BASE}/datasource/qc-db/test`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(patch),
  });
