// 全域認證狀態（Pinia）：token + 當前 user，token 與 localStorage 同步（真相源在 http 層）。
import { defineStore } from 'pinia';
import { computed, ref } from 'vue';
import {
  clearToken,
  getToken,
  setToken,
  login as apiLogin,
  register as apiRegister,
  getMe,
  type AuthUser,
} from '@/api';
import { usePermissionStore } from './permission.store';

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(getToken());
  const user = ref<AuthUser | null>(null);

  const isAuthed = (): boolean => !!token.value;

  /** 登入成功 → 存 token + user + 載入權限清單。失敗（401）由呼叫端 catch 顯示。 */
  async function login(email: string, password: string): Promise<void> {
    const res = await apiLogin(email, password);
    token.value = res.token;
    setToken(res.token);
    user.value = res.user;
    await usePermissionStore().load(); // 權限清單（be2 business-list），供 v-auth / 選單 / 守衛
  }

  /** 註冊成功 → 自動登入（存 token + user + 載入權限）。 */
  async function register(email: string, password: string): Promise<void> {
    const res = await apiRegister(email, password);
    token.value = res.token;
    setToken(res.token);
    user.value = res.user;
    await usePermissionStore().load();
  }

  /** 登出：清 token + user + 權限。 */
  function logout(): void {
    token.value = null;
    user.value = null;
    clearToken();
    usePermissionStore().clear();
  }

  /** 以既有 token 拉當前 user + 刷新權限；token 失效則登出。 */
  async function fetchMe(): Promise<void> {
    if (!token.value) return;
    try {
      user.value = await getMe();
      await usePermissionStore().load();
    } catch {
      logout();
    }
  }

  /** 是否 admin（規則發布 / config 編輯 / 恢復默認）；未載入 user 時保守視為非 admin。 */
  const isAdmin = computed(() => user.value?.role === 'admin');

  return { token, user, isAuthed, isAdmin, login, register, logout, fetchMe };
});
