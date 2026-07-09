// 後端錯誤 code → 前端 i18n 翻譯橋接。有 code 且 i18n 有對映則翻譯，否則回退後端 message（中文）。
import { ApiError } from '@/api';
import { i18n } from './index';

/**
 * 後端錯誤 code → i18n key（`errors.<CODE>`，如 AUTH.EMAIL_EXISTS → errors.AUTH.EMAIL_EXISTS）。
 * **唯一轉換點**：改對映規則只動這裡。
 */
export function errorCodeToI18nKey(code: string): string {
  return `errors.${code}`;
}

/**
 * 後端錯誤 → 可顯示訊息。
 * @param err j() 拋出的錯誤（ApiError 帶 code / status，或一般 Error）
 * @returns 有 code 且 i18n 有對映 → 翻譯文案；否則回退 err.message（後端已給中文）。
 */
export function translateApiError(err: unknown): string {
  if (err instanceof ApiError && err.code) {
    const key = errorCodeToI18nKey(err.code);
    const translated = i18n.global.t(key);
    if (translated !== key) return translated; // key 存在對映（vue-i18n 缺 key 時回傳 key 本身）
  }
  return err instanceof Error ? err.message : String(err);
}
