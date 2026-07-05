// 分頁選取字串解析（純函式）：'1,2,3~5' / '1~200' → 排序去重的頁碼清單。
// 支援分隔符：半形/全形逗號（, ，）；範圍符：~ - ～。非數字片段略過（防禦式）。

/**
 * 解析分頁選取字串為排序去重的頁碼陣列。
 * @param spec 如 '1,2,3~5' / '1~200' / '3，5'
 * @returns 升序去重頁碼；無有效頁碼回空陣列
 * @example parsePageSpec('1,3~5') // [1, 3, 4, 5]
 */
export function parsePageSpec(spec: string): number[] {
  const pages = new Set<number>();
  for (const part of spec.split(/[,，]/)) {
    const seg = part.trim();
    if (!seg) continue;
    const m = seg.split(/[~\-～]/);
    if (m.length === 2 && +m[0] && +m[1]) {
      for (let p = Math.min(+m[0], +m[1]); p <= Math.max(+m[0], +m[1]); p++) pages.add(p);
    } else if (+seg) {
      pages.add(+seg);
    }
  }
  return [...pages].sort((a, b) => a - b);
}
