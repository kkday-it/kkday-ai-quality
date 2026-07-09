// 權限判斷 composable：薄封裝 permission.store，給元件 template 用（v-if / :disabled）。
import { usePermissionStore } from '@/stores';
import type { PermissionKey } from '@/api';

/**
 * 取權限判斷工具。
 * @example const { can } = usePermission(); ... :disabled="!can(PERM.judgeRuleManage)"
 * @returns can 響應式權限判斷（permissions 變動時重算）；permissions 原始清單。
 */
export function usePermission() {
  const store = usePermissionStore();
  /** 是否具備某權限（在 template 呼叫即建立對 permissions 的響應式依賴）。 */
  const can = (key: PermissionKey | string): boolean => store.hasPermission(key);
  return { can, permissions: store.permissions };
}
