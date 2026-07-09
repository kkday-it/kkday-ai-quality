// 後端 FastAPI 共用請求層（dev 經 vite proxy /api → :8100）
export const BASE = '/api';

// JWT 真相源：localStorage（auth store 與 http 層共用同一 key）
const TOKEN_KEY = 'aiq_token';
export const getToken = (): string | null => localStorage.getItem(TOKEN_KEY);
export const setToken = (t: string): void => localStorage.setItem(TOKEN_KEY, t);
export const clearToken = (): void => localStorage.removeItem(TOKEN_KEY);

/**
 * API 錯誤：帶後端 `code`（DOMAIN.REASON，供 i18n 翻譯）+ HTTP `status`。
 * 無 code 的舊端點仍拋此類，`code` 為 undefined、`message` 即後端 detail 字串。
 */
export class ApiError extends Error {
  code?: string;
  status: number;
  constructor(message: string, status: number, code?: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

/**
 * 統一 fetch 包裝：自動帶 Authorization、處理 401、解析後端錯誤 detail（含 {code,message} 契約）。
 * @template T 回應 JSON 形狀（呼叫端 `j<Resp>(...)` 指定；預設 unknown，避免 any 洩漏）
 * @param url 完整請求路徑（含 BASE）
 * @param init 原生 fetch 選項
 * @returns 解析後的回應 JSON（型別為 T）
 * @throws {ApiError} 非 2xx 時拋出（message + status + 選填 code）；401 另清 token + 導向 /login
 */
export async function j<T = unknown>(url: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers = new Headers(init.headers);
  if (token) headers.set('Authorization', `Bearer ${token}`);

  const r = await fetch(url, { ...init, headers });
  if (!r.ok) {
    let message = `${r.status} ${url}`;
    let code: string | undefined;
    try {
      const e = await r.json();
      const d = e?.detail;
      if (typeof d === 'string') {
        message = d;
      } else if (d && typeof d === 'object') {
        // 後端 raise_api_error 契約：detail = {code, message}
        if (typeof d.code === 'string') code = d.code;
        if (typeof d.message === 'string') message = d.message;
        else if (!code) message = JSON.stringify(d);
      }
    } catch {
      /* 非 JSON 錯誤體，沿用 status 字串 */
    }
    if (r.status === 401) {
      clearToken();
      // 已在登入頁則不跳轉（避免帳密錯誤觸發整頁刷新、丟失錯誤訊息）
      if (!location.pathname.startsWith('/login')) location.assign('/login');
    }
    throw new ApiError(message, r.status, code);
  }
  return r.json() as Promise<T>;
}

/** 共用 JSON POST/PATCH header。 */
export const JSON_HEADERS = { 'Content-Type': 'application/json' } as const;
