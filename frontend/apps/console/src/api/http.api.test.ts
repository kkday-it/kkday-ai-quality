// AU9404 續期攔截測試（be2 模式）：refresh 成功重放一次、並發 single-flight、失敗落常規錯誤。
// vitest 跑 node 環境——自備最小 localStorage / document.cookie stub（測試檔自足，不引 jsdom 依賴）。
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { j } from './http.api';

// 強制 be2 模式（真 config authProvider=local；vi.mock hoisted、僅本檔生效）
vi.mock('@config/global/auth.config.json', () => ({
  default: {
    authProvider: 'be2',
    provider: 'local',
    be2: { authSvcUrl: 'https://auth.test', be2LoginUrl: 'https://be2.test/v2/auth/login' },
  },
}));

const store = new Map<string, string>();
const jar = new Map<string, string>();
Object.defineProperty(globalThis, 'localStorage', {
  configurable: true,
  value: {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
  },
});
Object.defineProperty(globalThis, 'document', {
  configurable: true,
  value: {
    get cookie() {
      return [...jar.entries()]
        .map(([k, v]) => `${k}=${v}`)
        .join('; ');
    },
    // 簡化語義：`k=v; path=/` 寫入、`max-age=0` 刪除（涵蓋 http.api 的兩種用法即可）
    set cookie(s: string) {
      const [pair, ...attrs] = s.split('; ');
      const eq = pair.indexOf('=');
      if (attrs.includes('max-age=0')) jar.delete(pair.slice(0, eq));
      else jar.set(pair.slice(0, eq), pair.slice(eq + 1));
    },
  },
});

/** j() 實際只碰 ok/status/headers/json——duck-typed 假 Response 即足。 */
interface FakeResponse {
  ok: boolean;
  status: number;
  headers: Headers;
  json: () => Promise<unknown>;
}
const okJson = (body: unknown): FakeResponse => ({
  ok: true,
  status: 200,
  headers: new Headers(),
  json: async () => body,
});
const au9404 = (): FakeResponse => ({
  ok: false,
  status: 403,
  headers: new Headers({ 'x-kkday-auth-svc-status': 'AU9404' }),
  json: async () => ({ detail: 'token expired' }),
});

describe('be2 AU9404 續期攔截（http.api j()）', () => {
  beforeEach(() => {
    store.clear();
    jar.clear();
    jar.set('authToken', 'old-token');
    store.set('auth.refresh-token', JSON.stringify({ value: 'rt-1', ttl: -1, startTime: 0 }));
  });

  it('403+AU9404 → refresh 成功 → 帶新 token 重放一次，RT 輪替寫回', async () => {
    const calls: string[] = [];
    const fetchMock = vi.fn(async (url: string, init?: RequestInit): Promise<FakeResponse> => {
      calls.push(url);
      if (url.includes('/refresh-token/'))
        return okJson({ data: { accessToken: 'new-token', refreshToken: 'rt-2' } });
      const auth = new Headers(init?.headers).get('Authorization');
      return auth === 'Bearer new-token' ? okJson({ ok: 1 }) : au9404();
    });
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch);

    expect(await j<{ ok: number }>('/api/x')).toEqual({ ok: 1 });
    expect(calls).toEqual(['/api/x', 'https://auth.test/api/v1/refresh-token/rt-1', '/api/x']);
    expect(jar.get('authToken')).toBe('new-token');
    expect((JSON.parse(store.get('auth.refresh-token') ?? '{}') as { value?: string }).value).toBe(
      'rt-2',
    );
  });

  it('並發兩請求同時 AU9404 → refresh 只打一次（single-flight）', async () => {
    let refreshCalls = 0;
    const fetchMock = vi.fn(async (url: string, init?: RequestInit): Promise<FakeResponse> => {
      if (url.includes('/refresh-token/')) {
        refreshCalls += 1;
        await new Promise((r) => setTimeout(r, 10)); // 拉長 refresh 窗，讓兩個 403 都排進等待
        return okJson({ data: { accessToken: 'new-token' } });
      }
      const auth = new Headers(init?.headers).get('Authorization');
      return auth === 'Bearer new-token' ? okJson({ ok: 1 }) : au9404();
    });
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch);

    const [a, b] = await Promise.all([j('/api/a'), j('/api/b')]);
    expect(a).toEqual({ ok: 1 });
    expect(b).toEqual({ ok: 1 });
    expect(refreshCalls).toBe(1);
  });

  it('refresh 失敗 → 拋 ApiError 403，不重放（原請求＋refresh 各一次）', async () => {
    const fetchMock = vi.fn(
      async (url: string): Promise<FakeResponse> =>
        url.includes('/refresh-token/')
          ? { ok: false, status: 401, headers: new Headers(), json: async () => ({}) }
          : au9404(),
    );
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch);

    await expect(j('/api/x')).rejects.toMatchObject({ status: 403 });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
