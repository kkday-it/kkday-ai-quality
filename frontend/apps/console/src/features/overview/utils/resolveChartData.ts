/**
 * 依 chartSpec 從 3-goal 資料集解析該圖資料（DashboardView 用）。
 * 以 spec.goal 決定取數：單一目標走 goals[goal]；'all' 跨目標彙整分支保留供未來「總覽」檢視——
 * dashboard.json 目前無 goal:'all' 條目，此分支尚未接線（規劃中）。
 */
import type { ChartSpec, GoalKey, Overview3 } from '../dashboard.types';
import type { NorthStarMetric, SourceRow } from '../types';

const GOAL_KEYS: GoalKey[] = ['content', 'presale', 'postsale'];

/** 解析單一圖表的資料；找不到回 undefined（external 等不需資料者亦回 undefined）。 */
export function resolveChartData(spec: ChartSpec, data: Overview3): unknown {
  // 全域資料（不分目標）：閉環流程 / 三大引擎
  if (spec.type === 'loop') return data.loop;
  if (spec.type === 'engines') return data.engines;
  if (spec.goal === 'all') {
    // 跨目標彙整（保留供未來總覽用；loop/engines 已於上方處理）
    if (spec.type === 'scorecard')
      return GOAL_KEYS.map((k) => data.goals[k]?.northStar[0]).filter(Boolean) as NorthStarMetric[];
    if (spec.type === 'trend') return data.crossTrend;
    if (spec.type === 'table')
      return GOAL_KEYS.flatMap((k) => data.goals[k]?.sources ?? []) as SourceRow[];
    return undefined;
  }
  const goal = data.goals[spec.goal];
  if (!goal) return undefined;
  if (spec.type === 'scorecard') return goal.northStar;
  if (spec.type === 'table') return goal.sources;
  if (spec.type === 'external') return undefined;
  if (spec.dataKey) return goal.charts[spec.dataKey];
  return undefined;
}
