/**
 * AI 質檢縱覽資料模型。
 * 對齊 mock/overview.mock3.json；接後端後改為 /api/overview 回傳同形狀，元件不需改。
 */

/** 北極星指標卡。tone＝core（核心落後·強調）/ lead（領先指標）。 */
export interface NorthStarMetric {
  key: string;
  label: string;
  value: number;
  unit: string;
  /** 數值目標（無則 null）；展示用文字另見 targetText */
  target: number | null;
  targetText: string;
  /** 環比變化量（百分點或絕對值，依指標語義） */
  delta: number;
  deltaDir: 'up' | 'down';
  /** 此次變化是否為正向（占比下降亦可為 good） */
  deltaGood: boolean;
  tone: 'core' | 'lead';
  hint: string;
  /** 近 6 期迷你趨勢 */
  spark: number[];
}

// 圖表通用契約（IntakeBreakdown / ReviewFunnel / TrendData）已下沉 @/shared/charts（被 judge/usage
// 共用）；此處 re-export 保持 overview 內部 `../types` 消費面不變。
export type { IntakeBreakdown, ReviewFunnel, TrendData } from '@/shared/charts';

/** 閉環引擎單一環節（流程圖步驟卡）。 */
export interface LoopStep {
  /** 步驟標題（含 ①–⑤ 序號） */
  name: string;
  /** 步驟說明 */
  sub: string;
}

/** 引擎卡內單一指標。 */
export interface EngineMetric {
  label: string;
  value: number;
  unit: string;
}

/** 三大引擎之一（AI 審品 / AI 內容撰寫 / AI 法官診斷）。 */
export interface EngineCard {
  id: string;
  name: string;
  tagline: string;
  pLevel: 'P0' | 'P1' | 'P2' | string;
  status: string;
  /** Arco tag color token */
  statusColor: string;
  goal: string;
  metrics: EngineMetric[];
  /** 迷你趨勢 sparkline 資料 */
  spark: number[];
  /** 可跳轉路由（無則卡片不可點，顯示 cta 為狀態文字） */
  route: string | null;
  cta: string;
}

/** 商品類別覆蓋列。 */
export interface CategoryCoverageRow {
  prod: string;
  pLevel: string;
  tier2: number;
  tier3: number;
  color: string;
}

/** 指標資料來源 / 外部儀表板連結。 */
export interface SourceRow {
  metric: string;
  kind: string;
  dapTable: string;
  dashboard: string;
  url: string;
}
