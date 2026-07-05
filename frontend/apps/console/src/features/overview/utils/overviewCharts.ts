/**
 * AI 質檢縱覽 ECharts option 建構器（純函式，無副作用）。
 * 元件只負責把 data 餵進對應 builder 並渲染 <v-chart>，配色 / 結構集中於此單一真相。
 * 圖表型別的 `use([...])` 註冊由消費頁面統一執行（見 DashboardView.vue），此處不耦合 echarts/core。
 */
import type { TrendData, IntakeBreakdown, ReviewFunnel } from '../types';
import type { GaugeData, BarData } from '../dashboard.types';

/** 品牌主色與輔色（對齊 Arco token，避免散落硬編碼）。 */
const C = {
  primary: '#165dff',
  green: '#00b42a',
  orange: '#ff7d00',
  red: '#f53f3f',
  gray: '#86909c',
  line: '#e5e6eb',
} as const;

/**
 * 引擎卡迷你趨勢 sparkline（無軸、無格線，僅一條漸層面積線）。
 * @param data 序列值
 * @param color 線色（依引擎主色）
 */
export function buildSparkOption(data: number[], color: string = C.primary) {
  return {
    grid: { left: 2, right: 2, top: 6, bottom: 2 },
    xAxis: { type: 'category', show: false, data: data.map((_, i) => i), boundaryGap: false },
    yAxis: { type: 'value', show: false, scale: true },
    tooltip: { trigger: 'axis', formatter: (p: { value: number }[]) => `${p[0].value}` },
    series: [
      {
        type: 'line',
        data,
        smooth: true,
        symbol: 'none',
        lineStyle: { color, width: 2 },
        areaStyle: { color, opacity: 0.12 },
      },
    ],
  };
}

/**
 * 指標趨勢折線圖（落後 / 領先共用）。
 * @param trend 趨勢資料；含 target 時加一條虛線目標基準線
 */
export function buildTrendOption(trend: TrendData) {
  const colors = [C.primary, C.green, C.orange, C.red];
  const series: Record<string, unknown>[] = trend.series.map((s, i) => ({
    name: s.name,
    type: 'line',
    smooth: true,
    symbolSize: 6,
    data: s.data,
    lineStyle: { width: 2.5, color: colors[i % colors.length] },
    itemStyle: { color: colors[i % colors.length] },
  }));

  // 目標基準線：用 markLine 掛在第一條序列上，視覺呈現達標門檻
  if (typeof trend.target === 'number' && series[0]) {
    series[0] = {
      ...series[0],
      markLine: {
        silent: true,
        symbol: 'none',
        lineStyle: { type: 'dashed', color: C.red, width: 1.5 },
        label: { formatter: `目標 ${trend.target}${trend.unit}`, color: C.red, fontSize: 11 },
        data: [{ yAxis: trend.target }],
      },
    };
  }

  return {
    tooltip: { trigger: 'axis', valueFormatter: (v: number) => `${v}${trend.unit}` },
    legend: { bottom: 0, textStyle: { color: C.gray, fontSize: 12 } },
    grid: { left: 8, right: 16, top: 16, bottom: 36, containLabel: true },
    xAxis: { type: 'category', boundaryGap: false, data: trend.months, axisLine: { lineStyle: { color: C.line } } },
    yAxis: {
      type: 'value',
      axisLabel: { formatter: `{value}${trend.unit}`, color: C.gray },
      splitLine: { lineStyle: { color: C.line } },
    },
    series,
  };
}

/**
 * 商品類別覆蓋橫向堆疊長條（Tier2 + Tier3 數量）。
 * @param rows 類別覆蓋列
 */
export function buildCoverageOption(rows: { prod: string; tier2: number; tier3: number; color: string }[]) {
  const prods = rows.map((r) => r.prod);
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: { bottom: 0, data: ['Tier2', 'Tier3'], textStyle: { color: C.gray } },
    grid: { left: 8, right: 24, top: 12, bottom: 36, containLabel: true },
    xAxis: { type: 'value', splitLine: { lineStyle: { color: C.line } }, axisLabel: { color: C.gray } },
    yAxis: { type: 'category', data: prods, axisLabel: { color: '#4e5969', fontWeight: 600 } },
    series: [
      {
        name: 'Tier2',
        type: 'bar',
        stack: 'cat',
        data: rows.map((r) => ({ value: r.tier2, itemStyle: { color: r.color } })),
        barWidth: 18,
        label: { show: true, position: 'inside', color: '#fff', fontSize: 11 },
      },
      {
        name: 'Tier3',
        type: 'bar',
        stack: 'cat',
        data: rows.map((r) => ({ value: r.tier3, itemStyle: { color: r.color, opacity: 0.45 } })),
        label: { show: true, position: 'inside', color: '#fff', fontSize: 11 },
      },
    ],
  };
}

/**
 * 售後進線結構分布甜甜圈：各進線類別占比，中心留白可置標題。
 * @param data 進線結構（各 item 自帶語義色）
 */
export function buildDonutOption(data: IntakeBreakdown) {
  return {
    tooltip: { trigger: 'item', valueFormatter: (v: number) => `${v}${data.unit}` },
    legend: { type: 'scroll', orient: 'vertical', right: 8, top: 'center', textStyle: { color: C.gray, fontSize: 12 } },
    series: [
      {
        type: 'pie',
        radius: ['46%', '70%'],
        center: ['38%', '50%'],
        avoidLabelOverlap: true,
        itemStyle: { borderColor: '#fff', borderWidth: 2 },
        label: { formatter: '{d}%', fontSize: 11, color: '#4e5969' },
        labelLine: { length: 8, length2: 8 },
        data: data.items.map((it) => ({ name: it.name, value: it.value, itemStyle: { color: it.color } })),
      },
    ],
  };
}

/**
 * 審品攔截漏斗：進件 → 必填 → 前審 → 後審 → 人工複核，逐級收斂的留存比例。
 * @param data 漏斗階段
 */
export function buildFunnelOption(data: ReviewFunnel) {
  const palette = [C.primary, '#4080ff', '#6aa1ff', '#94bfff', C.green];
  return {
    tooltip: { trigger: 'item', valueFormatter: (v: number) => `${v}${data.unit}` },
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
        label: { position: 'inside', formatter: '{b}：{c}%', color: '#fff', fontSize: 12 },
        itemStyle: { borderColor: '#fff', borderWidth: 1 },
        data: data.stages.map((s, i) => ({ name: s.name, value: s.value, itemStyle: { color: palette[i % palette.length] } })),
      },
    ],
  };
}

/**
 * 達成率儀表（gauge）：當前值對目標的進度。達標(value≥target)綠、否則藍；目標以刻度標籤標註。
 * @param g 量表資料（value / max / target / baseline）
 */
export function buildGaugeOption(g: GaugeData) {
  const reached = typeof g.target === 'number' && g.value >= g.target;
  const ratio = g.max > 0 ? Math.min(1, (g.target ?? g.max) / g.max) : 1;
  return {
    series: [
      {
        type: 'gauge',
        min: 0,
        max: g.max,
        startAngle: 210,
        endAngle: -30,
        progress: { show: true, width: 14, itemStyle: { color: reached ? C.green : C.primary } },
        axisLine: {
          lineStyle: {
            width: 14,
            // 目標門檻處變色：達標段淡綠、未達段淡灰
            color: [
              [ratio, C.line],
              [1, '#e8f3ff'],
            ],
          },
        },
        axisTick: { show: false },
        splitLine: { length: 10, lineStyle: { color: C.line } },
        axisLabel: { distance: 16, color: C.gray, fontSize: 10 },
        pointer: { width: 4, itemStyle: { color: reached ? C.green : C.primary } },
        // 目標基準：以一個額外刻度文字標註
        title: { show: true, offsetCenter: [0, '24%'], color: C.gray, fontSize: 11 },
        detail: {
          valueAnimation: true,
          formatter: `{v|{value}${g.unit}}`,
          rich: { v: { fontSize: 24, fontWeight: 700, color: reached ? C.green : C.primary } },
          offsetCenter: [0, '58%'],
        },
        data: [{ value: g.value, name: typeof g.target === 'number' ? `目標 ${g.target}${g.unit}` : '' }],
      },
    ],
  };
}

/**
 * 水平分類長條：各項數值，含資料標籤；有 target 時加一條垂直虛線目標基準。
 * @param d 長條資料（items + 可選 target）
 */
export function buildBarOption(d: BarData) {
  const names = d.items.map((it) => it.name);
  const hasTarget = typeof d.target === 'number';
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, valueFormatter: (v: number) => `${v}${d.unit}` },
    grid: { left: 8, right: 28, top: 12, bottom: hasTarget ? 28 : 12, containLabel: true },
    xAxis: {
      type: 'value',
      // hideOverlap：值域小（如成本 0~0.007USD）時刻度密集 + 單位後綴使標籤過寬會重疊，自動隱藏重疊者
      axisLabel: { formatter: `{value}${d.unit}`, color: C.gray, hideOverlap: true },
      splitLine: { lineStyle: { color: C.line } },
    },
    yAxis: { type: 'category', data: names, axisLabel: { color: '#4e5969', fontWeight: 600 } },
    series: [
      {
        type: 'bar',
        barWidth: 16,
        data: d.items.map((it) => ({ value: it.value, itemStyle: { color: it.color ?? C.primary, borderRadius: [0, 3, 3, 0] } })),
        label: { show: true, position: 'right', formatter: `{c}${d.unit}`, color: C.gray, fontSize: 11 },
        ...(hasTarget
          ? {
              markLine: {
                silent: true,
                symbol: 'none',
                lineStyle: { type: 'dashed', color: C.red, width: 1.5 },
                label: { formatter: `目標 ${d.target}${d.unit}`, color: C.red, fontSize: 11, position: 'insideEndTop' },
                data: [{ xAxis: d.target }],
              },
            }
          : {}),
      },
    ],
  };
}
