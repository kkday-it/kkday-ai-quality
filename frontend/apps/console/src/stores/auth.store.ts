// 全域身分狀態（Pinia）：本地模式為固定身分（無登入系統）；be2 模式 token 存 localStorage/cookie
// （真相源在 http 層），與 be2 SSO 對接時沿用同一套 token 讀寫。
import { defineStore } from 'pinia';
import { ref } from 'vue';
import { AUTH_PROVIDER, clearToken, getToken, getMe, type AuthUser } from '@/api';
import { usePermissionStore } from './permission.store';

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(getToken());
  const user = ref<AuthUser | null>(null);

  /** 本地模式恆為已認證（無登入系統，不需要 token）；be2 模式依 token 是否存在判定。 */
  const isAuthed = (): boolean => AUTH_PROVIDER !== 'be2' || !!token.value;

  /** 拉當前身分（本地模式為固定身分，免 token）+ 刷新權限清單；be2 模式 token 失效則清空身分。 */
  async function fetchMe(): Promise<void> {
    try {
      user.value = await getMe();
      await usePermissionStore().load(); // 權限清單（be2 business-list），供 usePermission() / 選單過濾
    } catch {
      if (AUTH_PROVIDER === 'be2') {
        token.value = null;
        clearToken();
        user.value = null;
        usePermissionStore().clear();
      }
    }
  }

  return { token, user, isAuthed, fetchMe };
});
