// i18n 框架單元測試：loader 接縫載入正確 namespace + 錯誤碼→i18n key 轉換。
import { describe, it, expect } from 'vitest';
import { loadLocaleMessages } from './loader';
import { errorCodeToI18nKey } from './apiError.util';

describe('i18n loader（唯一替換接縫）', () => {
  it('載入 zh-TW 並依 filename 分 namespace', async () => {
    const messages = (await loadLocaleMessages('zh-TW')) as Record<string, Record<string, unknown>>;
    expect(messages.common).toBeDefined();
    // key 結構 <namespace>.<page>.<語意>
    expect((messages.common.app as Record<string, string>).name).toBe('⚖️ AI 質檢');
  });

  it('未知語系回空 map', async () => {
    expect(await loadLocaleMessages('xx-YY')).toEqual({});
  });
});

describe('errorCodeToI18nKey（唯一轉換點）', () => {
  it('DOMAIN.REASON → errors.DOMAIN.REASON', () => {
    expect(errorCodeToI18nKey('DOMAIN.REASON')).toBe('errors.DOMAIN.REASON');
  });
});
