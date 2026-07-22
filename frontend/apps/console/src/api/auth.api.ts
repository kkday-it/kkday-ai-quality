// 當前身分 API：本地模式為固定身分（無登入系統），be2 模式為 SSO 解析出的使用者。
import { BASE, j } from './http.api';

export interface AuthUser {
  user_id: string;
  email: string;
}

/** 取當前身分（本地模式免 token；be2 模式帶 token 驗證）。 */
export const getMe = (): Promise<AuthUser> => j<AuthUser>(`${BASE}/auth/me`);
