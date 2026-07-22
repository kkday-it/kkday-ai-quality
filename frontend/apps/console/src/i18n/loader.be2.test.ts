// i18n loader be2 分支：uiLangList 成功攤平直餵、HTTP 失敗降級靜態 glob、Klingon 遞迴標注。
// vitest node 環境——自備最小 localStorage stub（同 http.api.test.ts 慣例，測試檔自足）。
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { loadLocaleMessages } from './loader';

// langPlatform 填實值 → LANG_PLATFORM_READY=true 走 be2 分支（vi.mock hoisted、僅本檔生效）
vi.mock('@config/global/auth.config.json', () => ({
  default: {
    authProvider: 'local',
    be2: { apiLangUrl: 'https://lang.test/api-lang', langPlatform: 'ai-quality' },
  },
}));

const store = new Map<string, string>();
Object.defineProperty(globalThis, 'localStorage', {
  configurable: true,
  value: {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
  },
});

describe('i18n loader be2 分支', () => {
  beforeEach(() => {
    store.clear();
  });

  it('uiLangList 成功 → 回攤平 {key: 譯文} map 直餵 vue-i18n', async () => {
    const fetchMock = vi.fn(async (url: string) => {
      expect(url).toBe('https://lang.test/api-lang/api/v1/uiLangList?lang[]=zh-TW&platform=ai-quality');
      return { ok: true, json: async () => ({ data: { 'zh-TW': { 'common.ok': '確定' } } }) };
    });
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch);

    expect(await loadLocaleMessages('zh-TW')).toEqual({ 'common.ok': '確定' });
  });

  it('uiLangList HTTP 失敗 → 降級靜態 glob（不阻斷啟動）', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: false, status: 500, json: async () => ({}) })) as unknown as typeof fetch,
    );

    const messages = (await loadLocaleMessages('zh-TW')) as Record<string, Record<string, unknown>>;
    expect(messages.common).toBeDefined(); // 靜態打包 namespace 仍在
  });

  it('Klingon 模式 → 每筆譯文前置 (完整key)（巢狀遞迴）', async () => {
    store.set('locale.klingon-active', '1');
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        json: async () => ({ data: { 'zh-TW': { 'common.ok': '確定' } } }),
      })) as unknown as typeof fetch,
    );

    expect(await loadLocaleMessages('zh-TW')).toEqual({ 'common.ok': '(common.ok) 確定' });

    // 巢狀（靜態降級路徑）：ns.page.key 逐層串出完整 key
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: false, status: 500, json: async () => ({}) })) as unknown as typeof fetch,
    );
    const nested = (await loadLocaleMessages('zh-TW')) as Record<string, Record<string, unknown>>;
    expect((nested.common.app as Record<string, string>).name).toBe('(common.app.name) ⚖️ AI 質檢');
  });
});
