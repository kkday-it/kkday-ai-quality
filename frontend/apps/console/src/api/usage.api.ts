// AI 消耗領域 API：LLM 使用紀錄多維度聚合（供「💰 AI 消耗」頁）。
import { BASE, j } from './http.api';

/** 某維度（模型/階段/來源）聚合一列。 */
export interface UsageGroupRow {
  key: string;
  cost: number;
  tokens: number;
  calls: number;
}

/** 趨勢一桶（依 granularity 為日/月/年）。 */
export interface UsageTrendRow {
  bucket: string;
  cost: number;
  tokens: number;
  calls: number;
}

/** AI 消耗聚合回應：KPI + 趨勢 + 各維度分布。 */
export interface UsageOverview {
  kpi: { cost: number; tokens: number; calls: number; cached: number };
  trend: UsageTrendRow[];
  by_model: UsageGroupRow[];
  by_stage: UsageGroupRow[];
  by_source: UsageGroupRow[];
}

/** AI 消耗查詢參數（日期區間 + 趨勢粒度）。 */
export interface UsageQuery {
  dateFrom?: string;
  dateTo?: string;
  granularity?: string;
}

/** 取 AI 消耗聚合（一次取齊 KPI + 趨勢 + 各模型/階段/來源分布）。 */
export const getUsageOverview = (q: UsageQuery = {}): Promise<UsageOverview> => {
  const p = new URLSearchParams();
  if (q.dateFrom) p.set('date_from', q.dateFrom);
  if (q.dateTo) p.set('date_to', q.dateTo);
  if (q.granularity) p.set('granularity', q.granularity);
  return j<UsageOverview>(`${BASE}/llm-usage/overview?${p.toString()}`);
};
