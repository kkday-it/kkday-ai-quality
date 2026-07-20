// i18n 訊息來源（**唯一可替換接縫**）：預設 Vite 靜態 glob 讀 locales/<locale>/*.json（filename=namespace）。
// be2 挖字接入（api-lang / Lokalise）：langPlatform 註冊完成後自動改走 fetchLangMap（失敗降級靜態）。
// Klingon debug 模式（借 be2 慣例）：localStorage `locale.klingon-active` 開啟後每筆譯文前置 `(key) `，
// 供 QA 肉眼核對「頁面字串是否都已挖字、對應哪個 key」。
import authConfig from '@config/global/auth.config.json';

type LocaleMessages = Record<string, unknown>;

/** be2 挖字端點設定（auth.config.json be2 段；langPlatform 為 REPLACE_ME＝尚未註冊，走靜態）。 */
const BE2 = (authConfig as { be2?: { apiLangUrl?: string; langPlatform?: string } }).be2 ?? {};
const LANG_PLATFORM_READY = !!BE2.apiLangUrl && !!BE2.langPlatform && !BE2.langPlatform.includes('REPLACE_ME');

/** Klingon 模式 flag（對齊 be2 慣例 key；`localStorage['locale.klingon-active']='1'` 開啟）。 */
export const isKlingonActive = (): boolean => localStorage.getItem('locale.klingon-active') === '1';

// eager：build 時靜態打包（zh-TW 目前唯一語系·體積小）。多語系 + 懶載時改 { eager: false } 動態 import。
const MODULES = import.meta.glob('../locales/*/*.json', { eager: true }) as Record<
  string,
  { default: unknown }
>;

/** 靜態 glob 訊息（local 預設來源＝be2 fetch 失敗時的降級）。 */
function staticMessages(locale: string): LocaleMessages {
  const messages: LocaleMessages = {};
  for (const path in MODULES) {
    const m = path.match(/\/locales\/([^/]+)\/([^/]+)\.json$/);
    if (!m || m[1] !== locale) continue;
    messages[m[2]] = MODULES[path].default;
  }
  return messages;
}

/**
 * be2 挖字來源：`GET {apiLangUrl}/api/v1/uiLangList?lang[]={locale}&platform={langPlatform}`
 * （Bearer accessToken·對齊 be2 admin 慣例）→ 回應 `data[locale]` 為攤平 `{key: 譯文}` map，
 * 直接餵 vue-i18n（flat key 相容）。任何失敗拋錯由呼叫端降級靜態（graceful）。
 */
async function fetchLangMap(locale: string): Promise<LocaleMessages> {
  const { getToken } = await import('@/api/http.api'); // 動態 import 防 api↔i18n 循環
  const token = getToken();
  const url = `${BE2.apiLangUrl}/api/v1/uiLangList?lang[]=${encodeURIComponent(locale)}&platform=${BE2.langPlatform}`;
  const r = await fetch(url, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!r.ok) throw new Error(`uiLangList ${r.status}`);
  const d = (await r.json()) as { data?: Record<string, Record<string, string>> };
  const map = d.data?.[locale];
  if (!map || typeof map !== 'object') throw new Error('uiLangList 回應無該語系 map');
  return map;
}

/** Klingon 標注：每筆字串譯文前置 `(key) `（巢狀遞迴；QA 核對挖字用）。 */
function klingonize(messages: LocaleMessages, prefix = ''): LocaleMessages {
  const out: LocaleMessages = {};
  for (const [k, v] of Object.entries(messages)) {
    const full = prefix ? `${prefix}.${k}` : k;
    out[k] = typeof v === 'string' ? `(${full}) ${v}` : klingonize(v as LocaleMessages, full);
  }
  return out;
}

/**
 * 載入某語系所有訊息（唯一接縫）。來源優先序：be2 api-lang（langPlatform 已註冊時）→ 靜態 glob 降級。
 * 保持 async 簽名：呼叫端（setupI18n）零改。
 * @param locale 語系碼（如 'zh-TW'）
 * @returns namespace → 訊息物件的 map（be2 來源為攤平 key map，vue-i18n flat 相容）
 */
export async function loadLocaleMessages(locale: string): Promise<LocaleMessages> {
  let messages: LocaleMessages;
  if (LANG_PLATFORM_READY) {
    try {
      messages = await fetchLangMap(locale);
    } catch {
      messages = staticMessages(locale); // be2 端點失敗 → 靜態降級（不阻斷啟動）
    }
  } else {
    messages = staticMessages(locale);
  }
  return isKlingonActive() ? klingonize(messages) : messages;
}
