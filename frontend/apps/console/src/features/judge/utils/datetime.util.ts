// 時間字串顯示正規化（純函式）。與後端 db.fmt_datetime 語義一致：評論時間含時分秒、出發日只到日。
// 自 useAttributionList 下沉為純 util（無反應式依賴），供多處復用並可單元測試。

/**
 * 正規化時間字串顯示：去小數秒 / 去 T·Z；dateOnly 或時間為 00:00:00 時只留日期。
 * @param value 原始時間字串（可能為 null/undefined）
 * @param dateOnly 是否強制只顯示日期
 * @returns 正規化後字串（無值回傳空字串）
 * @example fmtDt('2026-06-25T07:46:19.810Z') // '2026-06-25 07:46:19'
 * @example fmtDt('2026-07-01 00:00:00')      // '2026-07-01'
 */
export const fmtDt = (value: unknown, dateOnly = false): string => {
  if (value === null || value === undefined || value === '') return '';
  let s = String(value).trim().replace('T', ' ');
  if (s.endsWith('Z')) s = s.slice(0, -1).trim();
  s = s.replace(/\.\d+/, ''); // 去小數秒
  if (dateOnly || s.endsWith(' 00:00:00')) return s.split(' ')[0];
  return s;
};
