/**
 * 跨 feature 共用的圖表資料契約（ECharts builder 入參）。
 * 原散落 overview/types.ts 與 overview/dashboard.types.ts，被 judge / usage 反向依賴形成
 * feature 間耦合 → 下沉至 shared（feature → shared 單向依賴）；overview 原路徑保留 re-export。
 */

/** 趨勢圖資料（落後 / 領先共用）。 */
export interface TrendData {
  title: string;
  unit: string;
  months: string[];
  /** 目標基準線（僅落後指標有） */
  target?: number;
  series: { name: string; data: number[] }[];
}

/** 占比分布（甜甜圈）。 */
export interface IntakeBreakdown {
  title: string;
  unit: string;
  items: { name: string; value: number; color: string }[];
}

/** 漏斗（逐級收斂留存）。 */
export interface ReviewFunnel {
  title: string;
  unit: string;
  stages: { name: string; value: number }[];
}

/** 水平分類長條資料（各項 + 可選目標線）。 */
export interface BarData {
  title: string;
  unit: string;
  /** 目標基準線（垂直虛線）。 */
  target?: number;
  items: { name: string; value: number; color?: string }[];
}

/** 達成率儀表資料（gauge）。 */
export interface GaugeData {
  title: string;
  unit: string;
  value: number;
  /** 量表上限（如 CVR 目標 1.8 → max 可設 2）。 */
  max: number;
  /** 目標基準（達標門檻）。 */
  target?: number;
  baseline?: number;
}
