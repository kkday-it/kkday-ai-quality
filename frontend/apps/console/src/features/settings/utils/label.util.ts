// 設定 config 預設名稱用的時間戳。名稱只作唯一標籤（具體 provider/model、env/db 由卡片展示），
// 故新建時以時間戳產生，例：「LLM 202606301458」「QC DB 202606301458」，使用者可再改名。

/** 本地時區時間戳 YYYYMMDDHHmm（config 預設名稱用）。 */
export function configStamp(d: Date = new Date()): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}${p(d.getHours())}${p(d.getMinutes())}`;
}
