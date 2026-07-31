/**
 * 百分比顯示格式化（全站統一口徑）。
 *
 * ⚠️ **進度條一律顯示兩位小數**：Arco `a-progress` 的預設文字會把浮點原樣印出來
 * （`0.9863013698630137` → `98.63013698630137%`），一長串數字在窄容器裡還會把版面撐爆。
 * 專案規範見 `.claude/rules/frontend-vue.md`「進度條百分比」一節。
 */

/**
 * 把 Arco `a-progress` 的 0–1 百分比轉成 `xx.xx%` 顯示字串。
 *
 * @param percent Arco 口徑的比例值（0–1）；非數字／未知一律回 `—`（未知不等於 0%）。
 * @returns 形如 `98.63%` 的字串。
 * @example
 * ```vue
 * <a-progress :percent="pct">
 *   <template #text="{ percent }">{{ fmtPercent(percent) }}</template>
 * </a-progress>
 * ```
 */
export function fmtPercent(percent: number | null | undefined): string {
  if (percent === null || percent === undefined || !Number.isFinite(percent)) return '—';
  return `${(percent * 100).toFixed(2)}%`;
}
