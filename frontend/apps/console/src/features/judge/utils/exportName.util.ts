// 導出檔名統一規則：`<base>-<YYYYMMDDHHmmss>.<ext>`（本地秒級時間戳）。
// 所有導出（CSV / xlsx / PDF）共用，避免各處各寫一份命名而漂移。

/**
 * 組導出檔名：base + 本地秒級時間戳 + 副檔名，如 `判決規則-20260703100358.xlsx`。
 * @param base 檔名主體（可含來源等描述）
 * @param ext 副檔名（不含點，如 'csv' / 'xlsx' / 'pdf'）
 * @returns `<base>-<YYYYMMDDHHmmss>.<ext>`
 * @example exportName('判決規則', 'xlsx') // '判決規則-20260703100358.xlsx'
 */
export function exportName(base: string, ext: string): string {
  const d = new Date();
  const p = (n: number): string => String(n).padStart(2, '0');
  const ts = `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
  return `${base}-${ts}.${ext}`;
}
