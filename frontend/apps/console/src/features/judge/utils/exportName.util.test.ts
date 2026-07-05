import { describe, expect, it } from 'vitest';
import { exportName } from './exportName.util';

describe('exportName', () => {
  it('組 `<base>-<14 位秒級時間戳>.<ext>`', () => {
    expect(exportName('判決規則', 'xlsx')).toMatch(/^判決規則-\d{14}\.xlsx$/);
    expect(exportName('problems_all', 'csv')).toMatch(/^problems_all-\d{14}\.csv$/);
  });

  it('時間戳為本地時間、各段零填補（月/日/時/分/秒 兩位）', () => {
    const ts = exportName('x', 'pdf').slice(2, 16); // 去 'x-' 前綴、留 14 位
    expect(ts).toHaveLength(14);
    const mm = Number(ts.slice(4, 6));
    const dd = Number(ts.slice(6, 8));
    expect(mm).toBeGreaterThanOrEqual(1);
    expect(mm).toBeLessThanOrEqual(12);
    expect(dd).toBeGreaterThanOrEqual(1);
    expect(dd).toBeLessThanOrEqual(31);
  });
});
