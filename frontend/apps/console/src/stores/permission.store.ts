// 全域權限狀態（Pinia）：business-key 清單 + hasPermission；來源經 permission.api（唯一替換點）。
import { defineStore } from 'pinia';
import { ref } from 'vue';
import {
  cacheBusinessList,
  clearBusinessList,
  fetchPermissions,
  readCachedPermissions,
  type PermissionKey,
} from '@/api';

export const usePermissionStore = defineStore('permission', () => {
  // 初值先讀 localStorage 快取（避免重整後短暫「無權限」閃爍）；load() 再向後端刷新。
  const permissions = ref<string[]>(readCachedPermissions() ?? []);

  /** 向後端拉最新權限清單並快取；失敗時清空（fail-closed，不殘留舊權限）。 */
  async function load(): Promise<void> {
    try {
      const bl = await fetchPermissions();
      permissions.value = bl.value;
      cacheBusinessList(bl);
    } catch {
      permissions.value = [];
      clearBusinessList();
    }
  }

  /** 是否具備某 business-key 權限。 */
  function hasPermission(key: PermissionKey | string): boolean {
    return permissions.value.includes(key);
  }

  /** 清空權限（登出）。 */
  function clear(): void {
    permissions.value = [];
    clearBusinessList();
  }

  return { permissions, load, hasPermission, clear };
});
