// 權限清單來源（前端唯一替換點）：現讀後端 /api/auth/permissions（be2 auth.business-list 契約形狀）。
// 日後接 be2 中央 Auth SVC：**只改本檔 fetchPermissions**（改讀 be2 SDK / localStorage），
// permission.store / usePermission / 選單過濾全不動。
import { BASE, j } from './http.api';

/** be2 權限清單 localStorage key（與 be2 前端契約一致）。 */
export const AUTH_BUSINESS_LIST_KEY = 'auth.business-list';

/**
 * 權限清單回傳（be2 `auth.business-list` wire 契約形狀——僅 value/ttl 兩欄）。
 * @property value permission-string 陣列（module.sub-function.action）
 * @property ttl 快取存活毫秒
 */
export interface BusinessList {
  value: string[];
  ttl: number;
}

/**
 * 本地快取書記形狀——**對齊 be2 系 Cache wrapper**（`{value, ttl, startTime}`，見 be2-b2c-bs
 * bs-share Cache.js）：startTime 由前端寫入當下自記（ms epoch），配 ttl（秒→本專案沿用 ms，
 * 讀取時兼容兩者）判過期。與 be2 殼層同 key 同形狀＝未來同網域 localStorage 可互通。
 */
interface CachedBusinessList extends BusinessList {
  startTime: number;
}

/** business-key 常數（對齊後端 permission_keys.py；be2 風格 module.sub-function.action）。 */
export const PERM = {
  judgeRuleManage: 'judge-rule.version.manage',
  dataDatapackImport: 'data.datapack.import',
  dataDatapackExport: 'data.datapack.export',
  dataSourceUpload: 'data.source.upload',
  findingReviewUpdate: 'finding.review.update',
  problemListExport: 'problem.list.export',
  prejudgeRun: 'prejudge.run',
  settingsLlmConfigManage: 'settings.llm-config.manage',
  settingsLlmAreaDefaultWrite: 'settings.llm-area-default.write',
  settingsQcConfigManage: 'settings.qc-config.manage',
  settingsSecretRead: 'settings.secret.read',
} as const;

export type PermissionKey = (typeof PERM)[keyof typeof PERM];

/** 向後端取當前 user 權限清單（**唯一替換點**：換 be2 改此函式即可）。 */
export const fetchPermissions = (): Promise<BusinessList> =>
  j<BusinessList>(`${BASE}/auth/permissions`);

/** 寫入 localStorage（wire 契約＋自記 startTime——be2 Cache wrapper 同形狀，供跨頁 / 重整快取過期判斷）。 */
export function cacheBusinessList(bl: BusinessList): void {
  const cached: CachedBusinessList = { ...bl, startTime: Date.now() };
  localStorage.setItem(AUTH_BUSINESS_LIST_KEY, JSON.stringify(cached));
}

/**
 * 讀 localStorage 快取的權限清單（相容 be2 殼層寫入的同 key 資料）。
 * @returns 未過期回 permission-string 陣列；過期 / 無效 / 無快取回 null（過期即清除，對齊 be2 Cache 行為）。
 */
export function readCachedPermissions(): string[] | null {
  const raw = localStorage.getItem(AUTH_BUSINESS_LIST_KEY);
  if (!raw) return null;
  try {
    const bl = JSON.parse(raw) as CachedBusinessList;
    if (!Array.isArray(bl.value)) return null;
    // ttl 單位兼容：本後端回 ms（businessListTtlMs）；be2 殼層寫秒（86400）——小於 1e6 視為秒
    const ttlMs = bl.ttl > 0 && bl.ttl < 1_000_000 ? bl.ttl * 1000 : bl.ttl;
    if (ttlMs > 0 && bl.startTime && Date.now() > bl.startTime + ttlMs) {
      localStorage.removeItem(AUTH_BUSINESS_LIST_KEY); // 過期即清除（be2 Cache getItem 同行為）
      return null;
    }
    return bl.value;
  } catch {
    return null;
  }
}

/** 清除快取（登出用）。 */
export function clearBusinessList(): void {
  localStorage.removeItem(AUTH_BUSINESS_LIST_KEY);
}
