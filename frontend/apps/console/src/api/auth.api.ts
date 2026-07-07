// 帳號認證 API：註冊 / 登入 / 取當前使用者。
import { BASE, JSON_HEADERS, j } from './http.api';

export interface AuthUser {
  user_id: string;
  email: string;
  created_at?: string;
  /** 輕量 RBAC 角色（後端 config/global/roles.json 白名單即時派生）：admin｜qc。 */
  role?: 'admin' | 'qc';
}

export interface AuthResult {
  token: string;
  user: AuthUser;
}

/** 註冊新帳號 → 回 token + user（email 重複後端回 409）。 */
export const register = (email: string, password: string): Promise<AuthResult> =>
  j<AuthResult>(`${BASE}/auth/register`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ email, password }),
  });

/** 登入 → 回 token + user（帳密錯後端回 401）。 */
export const login = (email: string, password: string): Promise<AuthResult> =>
  j<AuthResult>(`${BASE}/auth/login`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ email, password }),
  });

/** 取當前登入使用者（帶 token 驗證）。 */
export const getMe = (): Promise<AuthUser> => j<AuthUser>(`${BASE}/auth/me`);
