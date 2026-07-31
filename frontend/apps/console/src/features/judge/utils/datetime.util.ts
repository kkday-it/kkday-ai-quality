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

/**
 * 將代表絕對時間的 ISO 字串轉為指定 IANA 時區。
 * 無法解析時沿用 fmtDt 的字串正規化，避免列表因單筆舊資料而整體渲染失敗。
 */
export const fmtDtInTimeZone = (value: unknown, timeZone: string, dateOnly = false): string => {
  if (value === null || value === undefined || value === '') return '';
  const date = value instanceof Date ? value : new Date(String(value));
  if (Number.isNaN(date.getTime())) return fmtDt(value, dateOnly);

  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    ...(dateOnly
      ? {}
      : {
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit',
          hourCycle: 'h23',
        }),
  }).formatToParts(date);
  const part = (type: Intl.DateTimeFormatPartTypes): string =>
    parts.find((item) => item.type === type)?.value ?? '';
  const dateText = `${part('year')}-${part('month')}-${part('day')}`;
  return dateOnly ? dateText : `${dateText} ${part('hour')}:${part('minute')}:${part('second')}`;
};

/** 後台操作時間的產品口徑：固定顯示北京時間（UTC+8），不跟隨瀏覽器所在時區。 */
export const fmtBeijingDt = (value: unknown, dateOnly = false): string =>
  fmtDtInTimeZone(value, 'Asia/Shanghai', dateOnly);

/**
 * 解析「時間點」為毫秒 epoch，同時吃 ISO 字串與 epoch 秒。
 *
 * 兩種格式並存是歷史包袱（初判 run 走 ISO、跑批快照的 `started_at` 曾是 epoch float），呼叫端不該
 * 為此各寫一次判斷。以 1e11 為界：epoch **秒**在可預見的未來都遠小於它，epoch **毫秒**則遠大於它。
 */
const toMs = (value: string | number | null | undefined): number | null => {
  if (value === null || value === undefined || value === '') return null;
  if (typeof value === 'number') return value < 1e11 ? value * 1000 : value;
  const ms = new Date(value).getTime();
  return Number.isNaN(ms) ? null : ms;
};

/**
 * 秒數 → 人可讀耗時：`<1s` / `42s` / `3m20s` / `1h02m`。
 *
 * @param seconds 秒數；`null`/`undefined`/負值回 `—`（未知不等於 0，寧可不顯示也不要編一個數字）。
 * @example fmtDurationSec(200) // '3m20s'
 */
export const fmtDurationSec = (seconds: number | null | undefined): string => {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds) || seconds < 0) return '—';
  const sec = Math.round(seconds);
  if (sec < 1) return '<1s';
  if (sec < 60) return `${sec}s`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m${String(sec % 60).padStart(2, '0')}s`;
  return `${Math.floor(sec / 3600)}h${String(Math.floor((sec % 3600) / 60)).padStart(2, '0')}m`;
};

/**
 * 由起訖時間算 run 耗時。
 *
 * @param start 起始時間（ISO 字串或 epoch 秒）；無值回 `—`。
 * @param end 結束時間；`null`/`undefined`＝尚未結束，改算到「現在」（執行中的 run 也能顯示已跑多久）。
 * @returns 人可讀的耗時字串；起訖無法解析時回 `—`。
 * @example fmtDuration('2026-07-31T08:00:00Z', '2026-07-31T08:03:20Z') // '3m20s'
 */
export const fmtDuration = (
  start: string | number | null | undefined,
  end?: string | number | null,
): string => {
  const from = toMs(start);
  if (from === null) return '—';
  return fmtDurationSec(((toMs(end) ?? Date.now()) - from) / 1000);
};
