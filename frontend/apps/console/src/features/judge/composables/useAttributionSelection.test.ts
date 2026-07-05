import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ref } from 'vue';
import type { ProblemRow } from '../constants';

// Arco Message 走 DOM → node 測試 mock 掉（selectPages 成功/失敗會呼叫）。
vi.mock('@arco-design/web-vue', () => ({ Message: { success: vi.fn(), error: vi.fn() } }));
// 只 mock getProblems（selectPages 用）；避免載入完整 api 層。
vi.mock('@/api', () => ({ getProblems: vi.fn() }));

import { getProblems } from '@/api';
import { useAttributionSelection } from './useAttributionSelection';

const getProblemsMock = vi.mocked(getProblems);
const filterQuery = () => ({ source: 'product_reviews' });
/** 建最小 ProblemRow（測試只用到 _group；其餘欄不影響選取邏輯）。 */
const pr = (g: string): ProblemRow => ({ _group: g }) as unknown as ProblemRow;
const mk = (pageSize = 20, rows: Partial<ProblemRow>[] = []) =>
  useAttributionSelection({
    rows: ref(rows as ProblemRow[]),
    pageSize: ref(pageSize),
    filterQuery,
  });

beforeEach(() => getProblemsMock.mockReset());

describe('useAttributionSelection', () => {
  it('onSelectionChange 跨頁合併：保留非本頁選取 + 併入本頁勾選', () => {
    const sel = mk(20, [{ _group: 'A' }, { _group: 'B' }]);
    sel.selectedKeys.value = ['X']; // 非本頁既有選取（不在 A/B 頁）
    sel.onSelectionChange(['A']); // 本頁只勾 A
    expect([...sel.selectedKeys.value].sort()).toEqual(['A', 'X']);
    expect(sel.runCount.value).toBe(2);
  });

  it('onSelectionChange 取消本頁勾選：本頁移除、非本頁保留', () => {
    const sel = mk(20, [{ _group: 'A' }, { _group: 'B' }]);
    sel.selectedKeys.value = ['A', 'B', 'X'];
    sel.onSelectionChange([]); // 本頁全取消（A/B 都不勾）
    expect(sel.selectedKeys.value).toEqual(['X']); // 只留非本頁 X
  });

  it('clearSelection 清空 + runCount 歸零', () => {
    const sel = mk();
    sel.selectedKeys.value = ['A', 'B'];
    sel.clearSelection();
    expect(sel.selectedKeys.value).toEqual([]);
    expect(sel.runCount.value).toBe(0);
  });

  it('selectPages：依分頁抓對應頁 id 併入選取（含 offset/limit 換算）', async () => {
    // 每頁 2 筆、選 1~2 頁 → offset=0 limit=4；回 4 列 → gp1(a,b)+gp2(c,d) 全中
    getProblemsMock.mockResolvedValue({ rows: [pr('a'), pr('b'), pr('c'), pr('d')], total: 4 });
    const sel = mk(2);
    sel.pageSpec.value = '1,2';
    await sel.selectPages();
    expect([...sel.selectedKeys.value].sort()).toEqual(['a', 'b', 'c', 'd']);
    expect(getProblemsMock).toHaveBeenCalledWith(
      expect.objectContaining({ source: 'product_reviews', offset: 0, limit: 4 }),
    );
  });

  it('selectPages：只選單一非首頁時 offset/limit 正確（page 2, size 2 → offset 2 limit 2）', async () => {
    getProblemsMock.mockResolvedValue({ rows: [pr('c'), pr('d')], total: 2 });
    const sel = mk(2);
    sel.pageSpec.value = '2';
    await sel.selectPages();
    expect([...sel.selectedKeys.value].sort()).toEqual(['c', 'd']);
    expect(getProblemsMock).toHaveBeenCalledWith(expect.objectContaining({ offset: 2, limit: 2 }));
  });

  it('selectPages：空 spec → 不撈不選', async () => {
    const sel = mk(2);
    sel.pageSpec.value = '';
    await sel.selectPages();
    expect(getProblemsMock).not.toHaveBeenCalled();
    expect(sel.selectedKeys.value).toEqual([]);
  });
});
