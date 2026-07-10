/**
 * 歸因概覽（AttributionOverview）資料契約 + 縱覽專屬 ECharts builder。
 *
 * 通用圖表（donut/bar/trend）直接複用 `@/shared/charts` 的既有 builder（契約吻合，
 * 不造輪子）；此處只放契約不合的新函式——count 語義漏斗（shared 的 buildFunnelOption
 * label 寫死「%」，無法呈現絕對筆數）。配色與 shared/charts builders 的品牌色一致。
 */

/** L1/L2/L3 等帶 code 的計數項（歸因域 / 細項分布）。 */
export interface CountItem {
  code: string;
  label: string;
  n: number;
}

/** 傾向分布項（positive/negative/neutral；unjudged＝未判 NULL 桶）。 */
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

/** 歸因概覽聚合回應（GET /api/problems/attribution_overview）。 */
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

/** L2/L3 細化列（下鑽 + 商品內容細化圖用）：筆數 + 多指標。 */
export interface BreakdownRow extends CountItem {
  /** 負向筆數 */
  neg: number;
  /** 平均信心（0~1，已四捨五入 3 位；無資料為 null） */
  avg_conf: number | null;
  /** 自動採信筆數（auto_accept tier；自動採信率＝auto/n） */
  auto: number;
  /** 父層 L2 code（僅 by_l3 帶；供前端點 L2 即時篩該 L2 下的 L3） */
  l2_code?: string;
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

/** 商品內容細化橫向長條的單筆資料（含全維度：筆數 / 占比 / 平均信心 / 自動採信率）。 */
export interface ContentBarItem {
  /** 細項顯示名（L2 面向 / L3 細項） */
  label: string;
  /** 筆數（＝負向筆數：僅負向才歸類，故不再拆負向/非負向） */
  n: number;
  /** 占比 %（同層總數為分母，呼叫端算好） */
  pct: number;
  /** 平均信心（0~1；無資料為 null） */
  avgConf: number | null;
  /** 自動採信率 %（呼叫端算好） */
  autoRate: number;
}

/**
 * 商品內容細化橫向長條（L2 面向 / L3 細項共用）：單序列筆數，tooltip 展全維度
 * （筆數 / 占比 / 平均信心 / 自動採信率）。因「僅負向才歸類」，筆數即負向數，
 * 不再拆負向/非負向堆疊（非負向恆 0，無意義）。
 *
 * 不複用 overview 的 buildBarOption——後者 tooltip 僅顯單值，無法一次帶多指標。
 * 橫向（category 在 y 軸）利於長中文細項名不截斷；資料量少故不做 dataZoom。
 * click 事件由呼叫端綁 `@click`（params.name＝category label）→ 反查 code 切換右側 L3。
 *
 * @param items 依筆數降序的細項（呼叫端已排序）；空陣列回空圖（呼叫端另顯 empty）
 * @returns ECharts option
 */
export function buildContentBarOption(items: ContentBarItem[]) {
  // ECharts category y 軸 index 0 在底部 → 反轉使最大值置頂，與表格由多到少一致
  const rev = [...items].reverse();
  const cats = rev.map((i) => i.label);
  const fmtConf = (v: number | null) => (v == null ? '—' : v.toFixed(2));
  return {
    grid: { left: 8, right: 16, top: 12, bottom: 8, containLabel: true },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      // 以 category index 反查原始細項，一次列出全維度（trigger:axis → params 為序列陣列，取首個的 dataIndex）
      formatter: (params: Array<{ dataIndex: number }>) => {
        const it = rev[params[0]?.dataIndex ?? 0];
        if (!it) return '';
        return (
          `${it.label}<br/>筆數：${it.n} · 占比：${it.pct}%<br/>` +
          `平均信心：${fmtConf(it.avgConf)} · 自動採信率：${it.autoRate}%`
        );
      },
    },
    xAxis: { type: 'value', minInterval: 1 },
    yAxis: { type: 'category', data: cats, axisLabel: { fontSize: 11, width: 96, overflow: 'truncate' } },
    series: [
      { name: '筆數', type: 'bar', barMaxWidth: 22, itemStyle: { color: '#165dff', borderRadius: [0, 3, 3, 0] }, data: rev.map((i) => i.n) },
    ],
  };
}
