import { describe, expect, it } from 'vitest';
import { fmtBeijingDt, fmtDt, fmtDtInTimeZone } from './datetime.util';

describe('fmtDt', () => {
  it('去 ISO 的 T/Z 與小數秒，保留時分秒', () => {
    expect(fmtDt('2026-06-25T07:46:19.810Z')).toBe('2026-06-25 07:46:19');
    expect(fmtDt('2026-06-25 07:46:19')).toBe('2026-06-25 07:46:19');
  });

  it('時間為 00:00:00 或 dateOnly 時只留日期', () => {
    expect(fmtDt('2026-07-01 00:00:00')).toBe('2026-07-01');
    expect(fmtDt('2026-07-01 09:30:00', true)).toBe('2026-07-01');
  });

  it('空值（null/undefined/空字串）回空字串', () => {
    expect(fmtDt(null)).toBe('');
    expect(fmtDt(undefined)).toBe('');
    expect(fmtDt('')).toBe('');
  });
});

describe('fmtDtInTimeZone', () => {
  it('將 UTC 時間換算為北京時間', () => {
    expect(fmtBeijingDt('2026-07-28T09:00:20+00:00')).toBe('2026-07-28 17:00:20');
    expect(fmtBeijingDt('2026-07-28T09:00:20Z')).toBe('2026-07-28 17:00:20');
  });

  it('依指定時區處理跨日並支援 dateOnly', () => {
    expect(fmtDtInTimeZone('2026-07-28T20:30:00Z', 'Asia/Shanghai')).toBe('2026-07-29 04:30:00');
    expect(fmtBeijingDt('2026-07-28T20:30:00Z', true)).toBe('2026-07-29');
  });

  it('無法解析時退回原有字串正規化', () => {
    expect(fmtBeijingDt('不是時間')).toBe('不是時間');
  });
});
