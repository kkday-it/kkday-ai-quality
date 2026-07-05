import { describe, expect, it } from 'vitest';
import { flatFinding } from './finding.util';

describe('flatFinding', () => {
  it('攤平巢狀 finding + 外層 meta 欄（外層欄覆蓋、finding 展開）', () => {
    const row = {
      finding_id: 'f1',
      prod_oid: 'P1',
      dimension: 'content',
      confidence: { value: 0.9, tier: 'auto_accept' },
      status: 'new',
      finding: {
        polarity: 'negative',
        l1: { code: 'content', label: '商品內容' },
        summary: '描述不符',
      },
    };
    const flat = flatFinding(row);
    // finding 子物件欄位展開至頂層
    expect(flat.polarity).toBe('negative');
    expect(flat.l1).toEqual({ code: 'content', label: '商品內容' });
    expect(flat.summary).toBe('描述不符');
    // 外層 meta 欄位保留
    expect(flat.finding_id).toBe('f1');
    expect(flat.prod_oid).toBe('P1');
    expect(flat.dimension).toBe('content');
    expect(flat.confidence).toEqual({ value: 0.9, tier: 'auto_accept' });
    expect(flat.status).toBe('new');
  });

  it('外層欄覆蓋 finding 內同名欄（meta 為權威）', () => {
    const flat = flatFinding({
      finding_id: 'outer',
      status: 'confirmed',
      finding: { finding_id: 'inner', status: 'new', extra: 1 },
    });
    expect(flat.finding_id).toBe('outer'); // 外層優先
    expect(flat.status).toBe('confirmed');
    expect(flat.extra).toBe(1); // finding 專有欄保留
  });
});
