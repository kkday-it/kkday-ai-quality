import { describe, expect, it } from 'vitest';
import { fmtDt } from './datetime.util';

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
