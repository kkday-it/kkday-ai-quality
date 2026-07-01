// 後端 FastAPI 共用請求層（dev 經 vite proxy /api → :8100）
export const BASE = '/api';

// JWT 真相源：localStorage（auth store 與 http 層共用同一 key）
const TOKEN_KEY = 'aiq_token';
export const getToken = (): string | null => localStorage.getItem(TOKEN_KEY);
export const setToken = (t: string): void => localStorage.setItem(TOKEN_KEY, t);
export const clearToken = (): void => localStorage.removeItem(TOKEN_KEY);

/**
 * 統一 fetch 包裝：自動帶 Authorization、處理 401、解析後端錯誤 detail。
 * @param url 完整請求路徑（含 BASE）
 * @param init 原生 fetch 選項
 * @throws {Error} 非 2xx 時拋出後端 `detail`（無則 `${status} ${url}`）；401 另清 token + 導向 /login
 */
export async function j(url: string, init: RequestInit = {}) {
  const token = getToken();
  const headers = new Headers(init.headers);
  if (token) headers.set('Authorization', `Bearer ${token}`);

  const r = await fetch(url, { ...init, headers });
  if (!r.ok) {
    let detail = `${r.status} ${url}`;
    try {
      const e = await r.json();
      if (e?.detail) detail = typeof e.detail === 'string' ? e.detail : JSON.stringify(e.detail);
    } catch {
      /* 非 JSON 錯誤體，沿用 status 字串 */
    }
    if (r.status === 401) {
      clearToken();
      // 已在登入頁則不跳轉（避免帳密錯誤觸發整頁刷新、丟失錯誤訊息）
      if (!location.pathname.startsWith('/login')) location.assign('/login');
    }
    throw new Error(detail);
  }
  return r.json();
}

/** 共用 JSON POST/PATCH header。 */
export const JSON_HEADERS = { 'Content-Type': 'application/json' } as const;
