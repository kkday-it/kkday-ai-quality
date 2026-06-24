// 設定面板的 localStorage 持久化：副作用收斂於此，UI 不直接碰 localStorage。
import { uniq } from 'lodash-es';
import {
  CACHE_KEY,
  OVERRIDES_KEY,
  PROVIDER_MODELS_KEY,
  type ModelOverride,
  type Provider,
} from '../constants';

/** base_url::model 組鍵。 */
export const modelKey = (base_url: string, model: string): string => `${base_url || ''}::${model || ''}`;

/** 讀全部 model 旋鈕記憶（壞檔回 {}）。 */
export function readOverrides(): Record<string, ModelOverride> {
  try {
    const raw = localStorage.getItem(OVERRIDES_KEY);
    return raw ? (JSON.parse(raw) as Record<string, ModelOverride>) : {};
  } catch {
    return {};
  }
}

/** 寫入單一 model 的旋鈕記憶。 */
export function writeOverride(base_url: string, model: string, ov: ModelOverride): void {
  const all = readOverrides();
  all[modelKey(base_url, model)] = ov;
  localStorage.setItem(OVERRIDES_KEY, JSON.stringify(all));
}

/** 供應商 → model 列表：preset 併 localStorage 自訂值（去重），純函式回傳合併結果。 */
export function mergeProviderModels(providers: Provider[]): Record<string, string[]> {
  const merged: Record<string, string[]> = {};
  for (const p of providers) merged[p.id] = [...p.defaultModels];
  try {
    const raw = localStorage.getItem(PROVIDER_MODELS_KEY);
    if (raw) {
      const custom = JSON.parse(raw) as Record<string, string[]>;
      for (const id of Object.keys(custom)) {
        const base = merged[id] ?? [];
        merged[id] = uniq([...base, ...custom[id]]);
      }
    }
  } catch {
    /* 壞檔忽略 */
  }
  return merged;
}

/** 持久化各供應商 model 列表。 */
export function persistProviderModels(models: Record<string, string[]>): void {
  localStorage.setItem(PROVIDER_MODELS_KEY, JSON.stringify(models));
}

/** 讀開頁快取（非敏感欄位；壞檔回 null）。 */
export function readSettingsCache<T>(): Partial<T> | null {
  try {
    const raw = localStorage.getItem(CACHE_KEY);
    return raw ? (JSON.parse(raw) as Partial<T>) : null;
  } catch {
    return null;
  }
}

/** 寫開頁快取。 */
export function writeSettingsCache(cache: Record<string, unknown>): void {
  localStorage.setItem(CACHE_KEY, JSON.stringify(cache));
}
