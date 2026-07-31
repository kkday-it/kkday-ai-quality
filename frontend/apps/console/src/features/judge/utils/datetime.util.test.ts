import { describe, expect, it } from 'vitest';
import { fmtBeijingDt, fmtDt, fmtDtInTimeZone, fmtDuration, fmtDurationSec } from './datetime.util';

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

describe('fmtDurationSec', () => {
  it('分級呈現：<1s / 秒 / 分秒 / 時分（分秒段零填補）', () => {
    expect(fmtDurationSec(0)).toBe('<1s');
    expect(fmtDurationSec(0.4)).toBe('<1s');
    expect(fmtDurationSec(42)).toBe('42s');
    expect(fmtDurationSec(200)).toBe('3m20s');
    expect(fmtDurationSec(65)).toBe('1m05s'); // 秒段補零，不是 1m5s
    expect(fmtDurationSec(3720)).toBe('1h02m');
  });

  it('未知（null/undefined/NaN/負值）回 — 而非 0——未知不等於沒花時間', () => {
    expect(fmtDurationSec(null)).toBe('—');
    expect(fmtDurationSec(undefined)).toBe('—');
    expect(fmtDurationSec(Number.NaN)).toBe('—');
    expect(fmtDurationSec(-5)).toBe('—');
  });
});

describe('fmtDuration', () => {
  it('ISO 起訖相減', () => {
    expect(fmtDuration('2026-07-31T08:00:00Z', '2026-07-31T08:03:20Z')).toBe('3m20s');
  });

  it('同時吃 epoch 秒（改造前跑批快照的舊格式）', () => {
    expect(fmtDuration(1785239284, 1785239326)).toBe('42s');
  });

  it('epoch 毫秒不會被誤當成秒（1e11 為界）', () => {
    expect(fmtDuration(1785239284000, 1785239326000)).toBe('42s');
  });

  it('缺結束時間＝尚未結束，算到現在', () => {
    const tenSecondsAgo = new Date(Date.now() - 10_000).toISOString();
    expect(fmtDuration(tenSecondsAgo)).toBe('10s');
  });

  it('缺起始時間回 —', () => {
    expect(fmtDuration(null)).toBe('—');
    expect(fmtDuration('')).toBe('—');
  });
});
