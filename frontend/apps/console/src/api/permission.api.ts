// 權限清單來源（前端唯一替換點）：現讀後端 /api/auth/permissions（be2 auth.business-list 契約形狀）。
// 日後接 be2 中央 Auth SVC：**只改本檔 fetchPermissions**（改讀 be2 SDK / localStorage），
// permission.store / usePermission / v-auth / router 守衛 / 選單過濾全不動。
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

/** 本地快取書記形狀（非 be2 契約欄位）：cachedAt 由前端寫入當下自記，配 ttl 判過期。 */
interface CachedBusinessList extends BusinessList {
  cachedAt: number;
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
} as const;

export type PermissionKey = (typeof PERM)[keyof typeof PERM];

/** 向後端取當前 user 權限清單（**唯一替換點**：換 be2 改此函式即可）。 */
export const fetchPermissions = (): Promise<BusinessList> =>
  j<BusinessList>(`${BASE}/auth/permissions`);

/** 寫入 localStorage（wire 契約＋前端自記 cachedAt，供跨頁 / 重整快取過期判斷）。 */
export function cacheBusinessList(bl: BusinessList): void {
  const cached: CachedBusinessList = { ...bl, cachedAt: Date.now() };
  localStorage.setItem(AUTH_BUSINESS_LIST_KEY, JSON.stringify(cached));
}

/**
 * 讀 localStorage 快取的權限清單。
 * @returns 未過期回 permission-string 陣列；過期 / 無效 / 無快取回 null。
 */
export function readCachedPermissions(): string[] | null {
  const raw = localStorage.getItem(AUTH_BUSINESS_LIST_KEY);
  if (!raw) return null;
  try {
    const bl = JSON.parse(raw) as CachedBusinessList;
    if (!Array.isArray(bl.value)) return null;
    if (bl.ttl && bl.cachedAt && Date.now() > bl.cachedAt + bl.ttl) return null; // 已過期
    return bl.value;
  } catch {
    return null;
  }
}

/** 清除快取（登出用）。 */
export function clearBusinessList(): void {
  localStorage.removeItem(AUTH_BUSINESS_LIST_KEY);
}
