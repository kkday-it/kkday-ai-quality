/**
 * 歸因縱覽（AttributionOverview）資料契約 + 縱覽專屬 ECharts builder。
 *
 * 通用圖表（donut/bar/trend）直接複用 `@/features/overview/utils` 的既有 builder（契約吻合，
 * 不造輪子）；此處只放契約不合的新函式——count 語義漏斗（overview 的 buildFunnelOption
 * label 寫死「%」，無法呈現絕對筆數）。配色與 overviewCharts 的品牌色一致。
 */

/** L1/L2/L3 等帶 code 的計數項（歸因域 / 細項分布）。 */
export interface CountItem {
  code: string;
  label: string;
  n: number;
}

/** 傾向分布項（positive/negative/neutral/unknown）。 */
export interface PolarityItem {
  polarity: string;
  label: string;
  n: number;
}

/** 星等分布項（1~5）。 */
export interface ScoreItem {
  score: number;
  n: number;
}

/** 信心分層計數（auto_accept/jury/needs_review）。 */
export interface TierCounts {
  auto_accept: number;
  jury: number;
  needs_review: number;
}

/** 月度時序（已判 / 負向）。 */
export interface TrendPayload {
  months: string[];
  judged: number[];
  negative: number[];
}

/** 歸因縱覽聚合回應（GET /api/problems/attribution_overview）。 */
export interface AttributionOverview {
  total_intake: number;
  judged: number;
  /** 已判且有 L1 歸因（即負向）。 */
  attributed: number;
  by_polarity: PolarityItem[];
  by_l1: CountItem[];
  by_tier: TierCounts;
  by_score: ScoreItem[];
  trend: TrendPayload;
}

/** L2/L3 細化列（下鑽 + 商品內容細化表用）：筆數 + 多指標。 */
export interface BreakdownRow extends CountItem {
  /** 負向筆數 */
  neg: number;
  /** 平均信心（0~1，已四捨五入 3 位；無資料為 null） */
  avg_conf: number | null;
  /** 自動採信筆數（auto_accept tier；自動採信率＝auto/n） */
  auto: number;
}

/** L1 下鑽回應（GET /api/problems/attribution_breakdown）。 */
export interface AttributionBreakdown {
  l1_code: string;
  l1_label: string;
  by_l2: BreakdownRow[];
  by_l3: BreakdownRow[];
}

/** 漏斗單一階段（絕對筆數語義）。 */
export interface FunnelStage {
  name: string;
  value: number;
}

/** 品牌主色與輔色（對齊 overviewCharts 的 C，避免散落硬編碼）。 */
const C = {
  primary: '#165dff',
  green: '#00b42a',
  line: '#e5e6eb',
} as const;

/**
 * 歸因漏斗（count 語義）：進線 → 已判 → 負向 → 已歸因，逐級收斂。
 *
 * 不複用 overview 的 buildFunnelOption——後者 label 寫死「{c}%」只能呈現百分比；本頁要看絕對
 * 筆數，故 label 顯示「{name}：{筆數}」，tooltip 另補相對首階段的留存率，兼顧絕對量與轉化感。
 * funnel 寬度以首階段為 max（min/max 取自資料）→ 寬度即正比於實際筆數，而非預設 0~100。
 *
 * @param stages 各階段（依序，筆數遞減）；首階段視為 100% 基準
 * @returns ECharts option
 */
export function buildAttrFunnelOption(stages: FunnelStage[]) {
  const palette = [C.primary, '#4080ff', '#6aa1ff', C.green];
  const base = stages[0]?.value || 0;
  return {
    tooltip: {
      trigger: 'item',
      // 顯示絕對筆數 + 相對首階段留存率（base 為 0 時不算比例，避免除以零）
      formatter: (p: { name: string; value: number }) => {
        const pct = base > 0 ? ((p.value / base) * 100).toFixed(1) : '—';
        return `${p.name}<br/>${p.value} 筆（${pct}%）`;
      },
    },
    series: [
      {
        type: 'funnel',
        left: 8,
        right: 8,
        top: 12,
        bottom: 12,
        minSize: '24%',
        maxSize: '100%',
        gap: 2,
        sort: 'none', // 維持傳入順序（已是遞減），不讓 ECharts 重排
        min: 0,
        max: base, // 寬度正比於實際筆數
        label: { position: 'inside', formatter: '{b}：{c}', color: '#fff', fontSize: 12 },
        itemStyle: { borderColor: '#fff', borderWidth: 1 },
        data: stages.map((s, i) => ({
          name: s.name,
          value: s.value,
          itemStyle: { color: palette[i % palette.length] },
        })),
      },
    ],
  };
}
