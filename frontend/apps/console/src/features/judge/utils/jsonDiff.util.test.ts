import { describe, expect, it } from 'vitest';
import { diffJsonPaths, jsonPathKey } from './jsonDiff.util';

describe('diffJsonPaths', () => {
  it('無變動 → 空集合、firstPath 為 null', () => {
    const r = diffJsonPaths({ a: 1, b: { c: 2 } }, { a: 1, b: { c: 2 } });
    expect(r.changed.size).toBe(0);
    expect(r.ancestors.size).toBe(0);
    expect(r.firstPath).toBeNull();
  });

  it('葉級修改 → 標記該葉 + 回填祖先（含根）', () => {
    const r = diffJsonPaths({ b: { c: 2 } }, { b: { c: 9 } });
    expect(r.changed.has(jsonPathKey(['b', 'c']))).toBe(true);
    expect(r.changed.has(jsonPathKey(['b']))).toBe(false); // 容器本身不染紅
    expect(r.ancestors.has(jsonPathKey([]))).toBe(true); // 根
    expect(r.ancestors.has(jsonPathKey(['b']))).toBe(true);
    expect(r.firstPath).toEqual(['b', 'c']);
  });

  it('新增 / 刪除鍵 → 標記該節點', () => {
    const added = diffJsonPaths({}, { x: 1 });
    expect(added.changed.has(jsonPathKey(['x']))).toBe(true);
    const removed = diffJsonPaths({ x: 1 }, {});
    expect(removed.changed.has(jsonPathKey(['x']))).toBe(true);
  });

  it('陣列以同索引逐位比對', () => {
    const r = diffJsonPaths({ list: [1, 2, 3] }, { list: [1, 9, 3] });
    expect(r.changed.has(jsonPathKey(['list', '1']))).toBe(true);
    expect(r.changed.has(jsonPathKey(['list', '0']))).toBe(false);
  });

  it('firstPath 優先「兩側皆有值的修改」而非純增刪', () => {
    // 先遇到新增（a 無 b 有），後遇到修改（兩側皆有）→ firstPath 應指向修改
    const r = diffJsonPaths({ m: 1 }, { added: 7, m: 2 });
    expect(r.changed.has(jsonPathKey(['added']))).toBe(true);
    expect(r.firstPath).toEqual(['m']);
  });

  it('型別變更（容器 ↔ 基本值）標記在該節點', () => {
    const r = diffJsonPaths({ k: { deep: 1 } }, { k: 5 });
    expect(r.changed.has(jsonPathKey(['k']))).toBe(true);
  });
});
