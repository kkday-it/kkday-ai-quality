/**
 * AI 質檢總體概覽 — config 驅動儀表板的型別。
 * 三個業務目標（content/presale/postsale）＋ 總覽（all）；圖表由 dashboard.json catalog 定義，
 * 資料由 goal-keyed 資料集（mock → /api/overview）提供。沿用既有 overview/types.ts 的圖表資料型別。
 */
import type { BarData, GaugeData } from '@/shared/charts';
import type {
  EngineCard,
  IntakeBreakdown,
  LoopStep,
  NorthStarMetric,
  ReviewFunnel,
  SourceRow,
  TrendData,
  CategoryCoverageRow,
} from './types';

/** 三個業務目標 key（對齊 Google Sheet roadmap）。 */
export type GoalKey = 'content' | 'presale' | 'postsale';

/** 圖表類型（對應 chartRegistry 的 renderer/builder）。 */
export type ChartType =
  | 'scorecard' // 北極星卡列（NorthStarCard grid）
  | 'trend' // 折線（含 target 虛線）
  | 'gauge' // 達成率儀表
  | 'donut' // 甜甜圈
  | 'funnel' // 漏斗
  | 'coverage' // 橫向堆疊長條（Tier2/Tier3）
  | 'bar' // 水平分類長條（可帶目標線）
  | 'loop' // 閉環流程（HTML）
  | 'engines' // 三大引擎卡列
  | 'table' // 指標來源表
  | 'external'; // 外部儀表板 link-out 卡

/** 單一圖表規格（dashboard.json catalog 的一條）。 */
export interface ChartSpec {
  id: string;
  title: string;
  type: ChartType;
  /** 歸屬目標；'all' 為跨目標（總覽用）。 */
  goal: GoalKey | 'all';
  kind?: 'lagging' | 'leading' | 'structural';
  /** 公共部分（跨目標重用，如落後 scorecard / 來源表）。 */
  common?: boolean;
  hint?: string;
  /** 資料出處標註（DAP 表 / 外部儀表板 URL）。 */
  source?: { dapTable?: string; dashboardUrl?: string };
  /** 在 goal data.charts[dataKey] 取圖表資料（scorecard/table/loop/engines/external 不需）。 */
  dataKey?: string;
  /** a-col 欄寬（1–24）；省略則 registry 給型別預設。 */
  grid?: number;
}

/** view 內的一個分區（結構分層：標題 + 該區圖表）。 */
export interface SectionSpec {
  title: string;
  /** 區塊說明（副標，選填）。 */
  desc?: string;
  charts: string[];
}

/** 一個 view（各業務目標）= 有序分區清單。 */
export interface ViewSpec {
  label: string;
  /** 此 view 綁定的資料目標（scorecard/table 取此 goal 的 northStar/sources）；'all' 為跨目標。 */
  goal: GoalKey | 'all';
  /** 分區結構（取代扁平 chartIds，提供清晰層次）。 */
  sections: SectionSpec[];
}

/** dashboard.json 全貌。 */
export interface DashboardConfig {
  views: Record<string, ViewSpec>;
  charts: Record<string, ChartSpec>;
}

/** 單一業務目標的資料集。 */
export interface GoalData {
  label: string;
  northStar: NorthStarMetric[];
  /** 圖表資料以 dataKey 索引；值型別依該圖 type（trend→TrendData…）。 */
  charts: Record<
    string,
    TrendData | IntakeBreakdown | ReviewFunnel | CategoryCoverageRow[] | GaugeData | BarData
  >;
  sources: SourceRow[];
}

// BarData / GaugeData 已下沉 @/shared/charts（被 judge/usage 共用）；re-export 保持消費面不變。
export type { BarData, GaugeData } from '@/shared/charts';

/** 3-goal 概覽資料集（mock / GET /api/overview 同形狀）。 */
export interface Overview3 {
  meta: { title: string; subtitle: string; period: string; note: string; loopCaption: string };
  loop: LoopStep[];
  engines: EngineCard[];
  /** 總覽用：跨三目標的落後指標趨勢（all.cross_trend）。 */
  crossTrend: TrendData;
  goals: Record<GoalKey, GoalData>;
}
