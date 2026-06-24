// 供應商推導：base_url ↔ provider id / 後端 provider 欄位。
import { PROVIDERS } from '../constants';

/** 由 base_url 反推 UI 供應商 id（後端只存 base_url，反查歸屬）。 */
export function deriveProviderId(base_url: string): string {
  const hit = PROVIDERS.find((p) => p.base_url === base_url);
  if (hit) return hit.id;
  if (base_url.includes('generativelanguage')) return 'gemini';
  if (base_url.includes('bytepluses')) return 'bytedance';
  return 'openai';
}

/**
 * 後端 provider 欄位（POST payload 用）：目前後端只區分 gemini / openai，
 * ByteDance 暫歸 openai（已知簡化；若後端支援 bytedance provider 需同步擴充此處）。
 */
export const deriveBackendProvider = (base_url: string): string =>
  base_url.includes('generativelanguage') ? 'gemini' : 'openai';
