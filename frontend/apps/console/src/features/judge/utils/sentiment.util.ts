// 情緒分（1-5 尺度）顯示色票：我方情緒分（our_sentiment）與外部評論情緒分／面向分（ext_sentiment、
// free_tag.tag_value）同尺度共用同一套分段定義，故收斂於此供列表 / 詳情抽屜 / 未來消費端共用。

/**
 * 情緒分 → 文字色 class：1-2 負向紅、3 中性琥珀、4-5 正向綠（對齊評論系統分段定義）。
 *
 * @param v 情緒分（1-5）。取 `unknown` 是因為來源多為 `ProblemRow` index signature 欄位；非數值
 *   或空值回預設文字色，不上色以免誤導。
 * @returns Tailwind 文字色 class
 */
export const sentimentClass = (v: unknown): string => {
  const n = Number(v);
  if (!Number.isFinite(n)) return 'text-[var(--color-text-1)]';
  if (n <= 2) return 'text-[rgb(var(--danger-6))]';
  if (n < 4) return 'text-[rgb(var(--warning-6))]';
  return 'text-[rgb(var(--success-6))]';
};

/**
 * 面向分 → Arco tag color：低分痛點紅、中性橙、高分綠（同 `sentimentClass` 分段，換 tag 色系）。
 *
 * @param v 面向分（free_tag.tag_value；非數值回中性灰）
 * @returns Arco `a-tag` 的 color 值
 */
export const sentimentTagColor = (v: unknown): string => {
  const n = Number(v);
  if (!Number.isFinite(n)) return 'gray';
  if (n <= 2) return 'red';
  if (n < 4) return 'orange';
  return 'green';
};
