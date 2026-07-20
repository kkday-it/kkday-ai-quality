// 後端 FastAPI 共用請求層（dev 經 vite proxy /api → :8100）
import authConfig from '@config/global/auth.config.json';

export const BASE = '/api';

/** 登入模式（auth.config.json authProvider·前後端同讀 SSOT）：local=自建 JWT｜be2=Auth Service token。 */
export const AUTH_PROVIDER: string = (authConfig as { authProvider?: string }).authProvider ?? 'local';
/** be2 接入佔位段（authSvcUrl/be2LoginUrl…；值待 platform 註冊回填）。 */
export const BE2_CONFIG: { authSvcUrl?: string; be2LoginUrl?: string } =
  (authConfig as { be2?: { authSvcUrl?: string; be2LoginUrl?: string } }).be2 ?? {};

// token 真相源（**唯一接縫**）：local=localStorage aiq_token（自建 JWT）；
// be2=Cookie `authToken`（Auth Service 寫入，與 be2 系後台共用，本前端唯讀）。
const TOKEN_KEY = 'aiq_token';
/** be2 refreshToken 存放（對齊 be2 Cache wrapper：localStorage `auth.refresh-token`＝{value,ttl,startTime}）。 */
const BE2_REFRESH_KEY = 'auth.refresh-token';

const readCookie = (name: string): string | null =>
  document.cookie
    .split('; ')
    .find((c) => c.startsWith(`${name}=`))
    ?.slice(name.length + 1) ?? null;

export const getToken = (): string | null =>
  AUTH_PROVIDER === 'be2' ? readCookie('authToken') : localStorage.getItem(TOKEN_KEY);
export const setToken = (t: string): void => {
  if (AUTH_PROVIDER === 'be2') document.cookie = `authToken=${t}; path=/`;
  else localStorage.setItem(TOKEN_KEY, t);
};
export const clearToken = (): void => {
  if (AUTH_PROVIDER === 'be2') {
    // be2 登出清整組（cookie + Cache wrapper 們；對齊 be2 系登出行為）
    document.cookie = 'authToken=; path=/; max-age=0';
    localStorage.removeItem(BE2_REFRESH_KEY);
    localStorage.removeItem('auth.business-list');
    localStorage.removeItem('locale.lang-map');
  } else {
    localStorage.removeItem(TOKEN_KEY);
  }
};

/** 讀 be2 refreshToken（Cache wrapper 解包；無/壞回 null）。 */
function getBe2RefreshToken(): string | null {
  try {
    const raw = localStorage.getItem(BE2_REFRESH_KEY);
    return raw ? ((JSON.parse(raw) as { value?: string }).value ?? null) : null;
  } catch {
    return null;
  }
}

/**
 * be2 token 續期（骨架·照抄 be2 系攔截 pattern）：403＋`x-kkday-auth-svc-status: AU9404`
 * 觸發 → `PATCH {authSvcUrl}/api/v1/refresh-token/{rt}` → 寫回新 token。
 * @returns 續期成功回 true（呼叫端重放原請求一次）；失敗 false（走登出導轉）。
 */
async function tryBe2Refresh(): Promise<boolean> {
  const rt = getBe2RefreshToken();
  const base = BE2_CONFIG.authSvcUrl;
  if (!rt || !base) return false;
  try {
    const r = await fetch(`${base}/api/v1/refresh-token/${rt}`, { method: 'PATCH' });
    if (!r.ok) return false;
    const d = (await r.json()) as { data?: { accessToken?: string; refreshToken?: string } };
    if (!d.data?.accessToken) return false;
    setToken(d.data.accessToken);
    if (d.data.refreshToken)
      localStorage.setItem(
        BE2_REFRESH_KEY,
        JSON.stringify({ value: d.data.refreshToken, ttl: -1, startTime: Date.now() }),
      );
    return true;
  } catch {
    return false;
  }
}

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

  let r = await fetch(url, { ...init, headers });
  // be2 模式續期攔截（骨架）：403＋AU9404（token 過期語義，對齊 api-gateway header 契約）
  // → refresh 成功即重放原請求一次；失敗落入下方 401/403 常規處理
  if (
    AUTH_PROVIDER === 'be2' &&
    r.status === 403 &&
    r.headers.get('x-kkday-auth-svc-status') === 'AU9404' &&
    (await tryBe2Refresh())
  ) {
    const retryHeaders = new Headers(init.headers);
    const fresh = getToken();
    if (fresh) retryHeaders.set('Authorization', `Bearer ${fresh}`);
    r = await fetch(url, { ...init, headers: retryHeaders });
  }
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
