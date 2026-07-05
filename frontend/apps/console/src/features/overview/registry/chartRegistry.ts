/**
 * 圖表 registry：集中註冊 ECharts 模組（use 一次）+ type → option builder 派發。
 * ChartCard 依 chartSpec.type 取 builder 產 option；非 ECharts 型別（scorecard/loop/engines/table/external）
 * 由 ChartCard 直接渲對應元件，不經此 builder。
 */
import { use } from 'echarts/core';
import { LineChart, BarChart, PieChart, FunnelChart, GaugeChart } from 'echarts/charts';
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  MarkLineComponent,
} from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import {
  buildTrendOption,
  buildDonutOption,
  buildFunnelOption,
  buildCoverageOption,
  buildGaugeOption,
  buildBarOption,
} from '../utils';
import type { ChartType, GaugeData, BarData } from '../dashboard.types';
import type { TrendData, IntakeBreakdown, ReviewFunnel, CategoryCoverageRow } from '../types';

// ECharts 模組單次註冊（涵蓋本模組所有 ECharts 圖型）。
use([
  LineChart,
  BarChart,
  PieChart,
  FunnelChart,
  GaugeChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  MarkLineComponent,
  CanvasRenderer,
]);

/** 以 ECharts <v-chart> 渲染的型別（其餘為 Vue 元件渲染）。 */
export const ECHARTS_TYPES: ReadonlySet<ChartType> = new Set<ChartType>([
  'trend',
  'donut',
  'funnel',
  'coverage',
  'gauge',
  'bar',
]);

export const isEchartsType = (t: ChartType): boolean => ECHARTS_TYPES.has(t);

/**
 * 依圖表型別 + 資料產 ECharts option。非 ECharts 型別回 null（由 ChartCard 走元件分支）。
 * @param type 圖表型別
 * @param data 該型別對應的資料（trend→TrendData…）
 */
export function buildOption(type: ChartType, data: unknown): Record<string, unknown> | null {
  switch (type) {
    case 'trend':
      return buildTrendOption(data as TrendData);
    case 'donut':
      return buildDonutOption(data as IntakeBreakdown);
    case 'funnel':
      return buildFunnelOption(data as ReviewFunnel);
    case 'coverage':
      return buildCoverageOption(data as CategoryCoverageRow[]);
    case 'gauge':
      return buildGaugeOption(data as GaugeData);
    case 'bar':
      return buildBarOption(data as BarData);
    default:
      return null;
  }
}
