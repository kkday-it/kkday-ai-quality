import { describe, expect, it } from 'vitest';
import { parsePageSpec } from './pageSpec.util';

describe('parsePageSpec', () => {
  it('單頁 / 逗號多頁 → 排序去重', () => {
    expect(parsePageSpec('3')).toEqual([3]);
    expect(parsePageSpec('3,1,2')).toEqual([1, 2, 3]);
    expect(parsePageSpec('2,2,2')).toEqual([2]); // 去重
  });

  it('範圍（~ / - / ～）展開', () => {
    expect(parsePageSpec('1~3')).toEqual([1, 2, 3]);
    expect(parsePageSpec('3-5')).toEqual([3, 4, 5]);
    expect(parsePageSpec('5~3')).toEqual([3, 4, 5]); // 反序範圍正規化
  });

  it('混合 + 全形逗號 + 去重合併', () => {
    expect(parsePageSpec('1,3~5，5')).toEqual([1, 3, 4, 5]);
  });

  it('空 / 純非數字 → 空陣列（防禦式略過壞片段）', () => {
    expect(parsePageSpec('')).toEqual([]);
    expect(parsePageSpec('  ,  ，')).toEqual([]);
    expect(parsePageSpec('abc,2,x')).toEqual([2]); // 略過非數字、保留有效頁
  });
});
