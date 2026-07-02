// 判決規則版本命名：以 created_at 秒級時間戳呈現（vYYYYMMDDHHmmss），取代流水號 int。
// 內部仍以 int version 排序/恢復（唯一鍵）；此處僅為顯示層命名，故舊版本亦由既有 created_at 自動糾正。

/**
 * 版本顯示名：created_at → `vYYYYMMDDHHmmss`（秒級）；缺 created_at 時回退 `v{version}`。
 * @param createdAt 版本建立時間（ISO 字串，如 2026-07-02T17:27:53...）
 * @param version 流水號（回退用）
 * @returns 版本顯示名（如 v20260702172753），皆無時回空字串
 * @example versionLabel('2026-07-02T17:27:53.123Z', 10) // 'v20260702172753'
 */
export function versionLabel(createdAt?: string | null, version?: number | null): string {
  if (createdAt) {
    const digits = createdAt.replace(/[^0-9]/g, '').slice(0, 14); // YYYYMMDDHHmmss
    if (digits.length === 14) return `v${digits}`;
  }
  return version != null ? `v${version}` : '';
}
