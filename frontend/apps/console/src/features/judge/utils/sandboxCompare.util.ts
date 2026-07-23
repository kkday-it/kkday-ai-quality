// Prompt 測試沙盒的對比純函式：差異判定與 metrics 摘要格式化。同批 item 的基準/草稿雙跑對比
// 與測試歷史勾兩筆的 run-vs-run 對比共用同一套判定與渲染，故獨立為純函式（無 Vue 響應式狀態）。
import type {
  PromptSandboxItemResult,
  PromptSandboxVariantResult,
  SandboxCompareMetrics,
} from '@/api';

/** 兩側結果是否有實質差異（極性不同或 (prompt_id, l2_code) 集合不同）——對比卡片標記用。 */
export function differs(
  a?: PromptSandboxVariantResult | PromptSandboxItemResult | null,
  b?: PromptSandboxVariantResult | PromptSandboxItemResult | null,
): boolean {
  if (!a || !b) return true;
  if (a.polarity !== b.polarity) return true;
  const key = (v: PromptSandboxVariantResult | PromptSandboxItemResult) =>
    (v.prompts ?? [])
      .flatMap((p) => (p.attributions ?? []).map((x) => `${p.prompt_id}:${x.l2_code}`))
      .sort()
      .join('|');
  return key(a) !== key(b);
}

/** metrics 顯示格式（null → —；比率 → 百分比）。 */
export function pct(v: number | null | undefined): string {
  return v == null ? '—' : `${Math.round(v * 1000) / 10}%`;
}

/** metrics 摘要條目（雙跑 run 與 run-vs-run 共用渲染）。 */
export function metricRows(
  m: SandboxCompareMetrics | null | undefined,
): { label: string; value: string }[] {
  return m
    ? [
        { label: '極性一致', value: pct(m.polarity_agree) },
        { label: '情緒分一致', value: pct(m.sentiment_agree) },
        { label: '歸因 Jaccard', value: pct(m.facet_jaccard_mean) },
        { label: '主歸因一致', value: pct(m.primary_agree) },
        { label: '筆數一致', value: pct(m.count_equal) },
      ]
    : [];
}
