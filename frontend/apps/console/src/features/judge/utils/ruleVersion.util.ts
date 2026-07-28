// 初判規則版本顯示名。初判 Prompt（prompt_*）自 2026-07-28 起改存檔案版本庫，版本識別本身
// 就是 `vYYYYMMDDHHmmss`（＝檔名），直接顯示即可；其餘 rule（bd_tag_vertical / source_mapping）
// 仍是 DB 流水號 int，維持既有「以 created_at 推導時間戳」的顯示慣例。

import type { RuleVersion } from '@/api/judgeRules.api';

/** 檔案版本庫的版本識別格式（定長時間戳，字典序即時序）。 */
const FILE_VERSION_RE = /^v\d{14}$/;

/**
 * 版本顯示名。
 *
 * 優先序刻意是「版本識別本身 → created_at 推導 → 流水號回退」：檔案版本庫的版本已經是
 * `v20260724041913` 這種形式，若還走回退分支會被再加一個 v 變成 `vv2026...`。
 *
 * @param createdAt 版本建立時間（ISO 字串，如 2026-07-02T17:27:53...）
 * @param version 版本識別（檔案版本庫為 `v...` 字串；DB rule 為流水號）
 * @returns 版本顯示名（如 v20260702172753），皆無時回空字串
 * @example versionLabel('2026-07-02T17:27:53.123Z', 10) // 'v20260702172753'
 * @example versionLabel(null, 'v20260724041913') // 'v20260724041913'（不重複加 v）
 */
export function versionLabel(createdAt?: string | null, version?: RuleVersion | null): string {
  if (typeof version === 'string' && FILE_VERSION_RE.test(version)) return version;
  if (createdAt) {
    const digits = createdAt.replace(/[^0-9]/g, '').slice(0, 14); // YYYYMMDDHHmmss
    if (digits.length === 14) return `v${digits}`;
  }
  return version != null ? `v${version}` : '';
}
