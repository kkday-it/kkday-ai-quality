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

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(getToken());
  const user = ref<AuthUser | null>(null);

  const isAuthed = (): boolean => !!token.value;

  /** 登入成功 → 存 token + user。失敗（401）由呼叫端 catch 顯示。 */
  async function login(email: string, password: string): Promise<void> {
    const res = await apiLogin(email, password);
    token.value = res.token;
    setToken(res.token);
    user.value = res.user;
  }

  /** 註冊成功 → 自動登入（存 token + user）。 */
  async function register(email: string, password: string): Promise<void> {
    const res = await apiRegister(email, password);
    token.value = res.token;
    setToken(res.token);
    user.value = res.user;
  }

  /** 登出：清 token + user。 */
  function logout(): void {
    token.value = null;
    user.value = null;
    clearToken();
  }

  /** 以既有 token 拉當前 user；token 失效則登出。 */
  async function fetchMe(): Promise<void> {
    if (!token.value) return;
    try {
      user.value = await getMe();
    } catch {
      logout();
    }
  }

  /** 是否 admin（規則發布 / config 編輯 / 恢復默認）；未載入 user 時保守視為非 admin。 */
  const isAdmin = computed(() => user.value?.role === 'admin');

  return { token, user, isAuthed, isAdmin, login, register, logout, fetchMe };
});
