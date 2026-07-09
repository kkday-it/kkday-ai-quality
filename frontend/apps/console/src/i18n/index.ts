// i18n 框架入口：vue-i18n（Composition API 模式）+ 訊息載入（經 loader 單一接縫）+ 錯誤翻譯橋接。
import { createI18n } from 'vue-i18n';
import { loadLocaleMessages } from './loader';

/** 目前唯一語系（zh-TW）。多語系時擴充此處 + 新增 locales/<locale>/。 */
export const DEFAULT_LOCALE = 'zh-TW';

export const i18n = createI18n({
  legacy: false, // Composition API 模式（useI18n / t）
  globalInjection: true, // template 可直接用 $t，免每個 setup 引 useI18n
  locale: DEFAULT_LOCALE,
  fallbackLocale: DEFAULT_LOCALE,
  messages: {}, // 由 setupI18n 經 loader 載入（唯一替換接縫）
});

/**
 * 載入語系訊息並套用（app 啟動於 mount 前 await）。換翻譯來源（TMS）只需改 loader.ts。
 * @param locale 目標語系（預設 zh-TW）
 */
export async function setupI18n(locale: string = DEFAULT_LOCALE): Promise<void> {
  const messages = await loadLocaleMessages(locale);
  i18n.global.setLocaleMessage(locale, messages as Record<string, unknown>);
  i18n.global.locale.value = locale;
}

export { errorCodeToI18nKey, translateApiError } from './apiError.util';
