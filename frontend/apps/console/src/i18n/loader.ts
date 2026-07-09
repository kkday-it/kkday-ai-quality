// i18n 訊息來源（**唯一可替換接縫**）：現以 Vite 靜態 glob 讀 locales/<locale>/*.json（filename=namespace）。
// 日後接 TMS 只改本函式（改 fetch 遠端後回傳同形狀 map），i18n instance / $t / 元件全不動。
type LocaleMessages = Record<string, unknown>;

// eager：build 時靜態打包（zh-TW 目前唯一語系·體積小）。多語系 + 懶載時改 { eager: false } 動態 import。
const MODULES = import.meta.glob('../locales/*/*.json', { eager: true }) as Record<
  string,
  { default: unknown }
>;

/**
 * 載入某語系所有 namespace 訊息（filename = namespace，如 auth.json → messages.auth）。
 * 保持 async 簽名：日後換 fetch TMS 時呼叫端零改（現靜態 glob 為同步，包成已解析 Promise）。
 * @param locale 語系碼（如 'zh-TW'）
 * @returns namespace → 訊息物件的 map
 */
export async function loadLocaleMessages(locale: string): Promise<LocaleMessages> {
  const messages: LocaleMessages = {};
  for (const path in MODULES) {
    const m = path.match(/\/locales\/([^/]+)\/([^/]+)\.json$/);
    if (!m || m[1] !== locale) continue;
    messages[m[2]] = MODULES[path].default;
  }
  return messages;
}
