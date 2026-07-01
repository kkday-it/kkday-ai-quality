// config/taxonomy/*.json 線上查看 / 編輯 API（規則 tab 的 JSON 編輯器）。
// 後端 /api/config：讀回解析後 content；寫入前後端做 JSON 驗證 + .bak 備份 + taxonomy.reload()。
import { BASE, JSON_HEADERS, j } from './http.api';

/** 可編輯 config 檔清單項。 */
export interface ConfigFileMeta {
  /** 相對 config/taxonomy/ 的 posix 路徑（如 'domains.json' / 'mappings/xxx.json'）。 */
  name: string;
  bytes: number;
}

/** 讀單一 config 檔的回傳。 */
export interface ConfigFileContent {
  name: string;
  /** 已解析的 JSON 值（物件 / 陣列）。 */
  content: unknown;
  /** 原始檔字串（保留磁碟上的原始格式）。 */
  text: string;
}

/** 寫入結果。reloaded=false 表示後端重載失敗（多半是改壞結構），需提示使用者。 */
export interface ConfigWriteResult {
  ok: boolean;
  name: string;
  bytes: number;
  reloaded: boolean;
}

/** 列出所有可編輯 config 檔。 */
export const listConfigFiles = (): Promise<ConfigFileMeta[]> => j(`${BASE}/config/files`);

/** 讀單一 config 檔（name 為相對路徑，內部自動 encode 各 segment）。 */
export const getConfigFile = (name: string): Promise<ConfigFileContent> =>
  j(`${BASE}/config/files/${encodePath(name)}`);

/** 覆寫單一 config 檔；content 為已解析的 JSON 值。 */
export const saveConfigFile = (name: string, content: unknown): Promise<ConfigWriteResult> =>
  j(`${BASE}/config/files/${encodePath(name)}`, {
    method: 'PUT',
    headers: JSON_HEADERS,
    body: JSON.stringify({ content }),
  });

/** 逐段 encode 相對路徑，保留 `/` 分隔（後端用 {name:path} 接收）。 */
function encodePath(name: string): string {
  return name.split('/').map(encodeURIComponent).join('/');
}
