// 版本對比 JSON diff：計算兩版之間「有變動節點」的 path 集合，供 vanilla-jsoneditor
// onClassName 標紅、expand 展開祖先、scrollTo 對齊定位使用。純函式無副作用。
import { isEqual } from 'lodash-es';

/** path 分隔符：用不可見控制字元，避免與真實 JSON key 內容碰撞。 */
const SEP = '\u0001';

/**
 * 將 JSONPath（vanilla-jsoneditor 的 `string[]`）序列化為可作 Set key 的穩定字串。
 * 根節點 `[]` → 空字串 `''`。
 * @param path 節點路徑（各段皆為字串；陣列索引以字串表示）
 */
export const jsonPathKey = (path: ReadonlyArray<string>): string => path.join(SEP);

/** 深度比對結果：供高亮 / 展開 / 對齊三用途。 */
export interface JsonDiffResult {
  /** 值有變動（改 / 增 / 刪）節點的 path key 集合 → onClassName 標紅。 */
  changed: Set<string>;
  /** 所有變動節點的祖先容器 path key（含根 `''`）→ expand 逐層展開至變動處。 */
  ancestors: Set<string>;
  /** 首個變動節點 path（優先兩側皆有值的「修改」，供雙欄 scrollTo 對齊）；無變動時為 null。 */
  firstPath: string[] | null;
}

/** 物件或陣列（可再往下走的容器節點）。 */
const isContainer = (v: unknown): v is Record<string, unknown> | unknown[] =>
  typeof v === 'object' && v !== null;

/**
 * 深度比對兩個 JSON，回傳變動節點 path 集合 + 祖先集合 + 首個變動 path。
 *
 * 設計取捨：陣列以「同索引」逐位比對（config 屬鍵控結構，不做 LCS 移動偵測 —— 足夠且結果穩定，
 * 避免過度工程）。同為容器且同型（皆物件或皆陣列）時往下遞迴，只把真正的葉級差異 / 型別變更 /
 * 增刪標記在最接近的節點上，讓高亮聚焦「值」而非整棵子樹。
 *
 * @param a 前版 JSON
 * @param b 後版 JSON
 * @returns 變動 path 集合、祖先集合、首個變動 path
 * @example diffJsonPaths({ x: 1 }, { x: 2 }).changed // Set { 'x' }
 */
export function diffJsonPaths(a: unknown, b: unknown): JsonDiffResult {
  const changed = new Set<string>();
  const ancestors = new Set<string>();
  let firstBoth: string[] | null = null; // 兩側皆有值的修改（對齊優先）
  let firstAny: string[] | null = null; // 任意首個變動（無修改時退用）

  /** 標記變動節點並回填其所有祖先容器（含根）。 */
  const mark = (path: string[], bothSides: boolean): void => {
    changed.add(jsonPathKey(path));
    for (let i = 0; i < path.length; i++) ancestors.add(jsonPathKey(path.slice(0, i)));
    if (!firstAny) firstAny = path;
    if (bothSides && !firstBoth) firstBoth = path;
  };

  const walk = (x: unknown, y: unknown, path: string[]): void => {
    if (isEqual(x, y)) return;
    const sameKind = isContainer(x) && isContainer(y) && Array.isArray(x) === Array.isArray(y);
    if (sameKind) {
      const keys = Array.isArray(x)
        ? Array.from({ length: Math.max(x.length, (y as unknown[]).length) }, (_, i) => String(i))
        : Array.from(new Set([...Object.keys(x as object), ...Object.keys(y as object)]));
      for (const k of keys) {
        walk((x as Record<string, unknown>)[k], (y as Record<string, unknown>)[k], [...path, k]);
      }
      return;
    }
    // 葉級差異 / 型別變更（容器↔基本值）/ 增（x undefined）/ 刪（y undefined）
    mark(path, x !== undefined && y !== undefined);
  };

  walk(a, b, []);
  return { changed, ancestors, firstPath: firstBoth ?? firstAny };
}
