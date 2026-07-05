// AI 消耗聚合 composable：抓 /api/llm-usage/overview → 餵 overviewCharts 現成 builder（不造輪子）。
import { computed, ref, toValue, watch, type MaybeRefOrGetter } from 'vue';
import { getUsageOverview, type UsageGroupRow, type UsageOverview, type UsageQuery } from '@/api';
import { buildBarOption, buildTrendOption } from '@/features/overview/utils';
import type { BarData } from '@/features/overview/dashboard.types';
import type { TrendData } from '@/features/overview/types';

/** 群組列 → buildBarOption 的 BarData（value 取成本 USD，依成本降冪已由後端排好）。 */
function toBar(rows: UsageGroupRow[] | undefined, title: string): BarData {
  return {
    title,
    unit: 'USD',
    items: (rows ?? []).map((r) => ({ name: r.key, value: Number(r.cost.toFixed(4)) })),
  };
}

/**
 * AI 消耗 dashboard 資料 + 圖表 option。
 * @param query 查詢（dateFrom/dateTo/granularity；響應式，變更自動重載）
 */
export function useUsageDashboard(query: MaybeRefOrGetter<UsageQuery>) {
  const loading = ref(false);
  const error = ref('');
  const data = ref<UsageOverview | null>(null);

  /** 重新抓聚合資料。 */
  const reload = async () => {
    loading.value = true;
    error.value = '';
    try {
      data.value = await getUsageOverview(toValue(query));
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e);
    } finally {
      loading.value = false;
    }
  };
  watch(() => toValue(query), reload, { immediate: true, deep: true });

  const hasData = computed(() => (data.value?.kpi.calls ?? 0) > 0);
  const kpi = computed(() => data.value?.kpi ?? { cost: 0, tokens: 0, calls: 0, cached: 0 });
  const buckets = computed(() => data.value?.trend.map((t) => t.bucket) ?? []);

  const costTrend = computed<TrendData>(() => ({
    title: '每日成本',
    unit: 'USD',
    months: buckets.value,
    series: [{ name: '成本', data: data.value?.trend.map((t) => Number(t.cost.toFixed(6))) ?? [] }],
  }));
  const callsTrend = computed<TrendData>(() => ({
    title: '每日呼叫數',
    unit: '次',
    months: buckets.value,
    series: [{ name: '呼叫', data: data.value?.trend.map((t) => t.calls) ?? [] }],
  }));

  return {
    loading,
    error,
    hasData,
    kpi,
    reload,
    costTrendOption: computed(() => buildTrendOption(costTrend.value)),
    callsTrendOption: computed(() => buildTrendOption(callsTrend.value)),
    byModelOption: computed(() => buildBarOption(toBar(data.value?.by_model, '各模型成本'))),
    byStageOption: computed(() => buildBarOption(toBar(data.value?.by_stage, '各階段成本'))),
    bySourceOption: computed(() => buildBarOption(toBar(data.value?.by_source, '各來源成本'))),
  };
}
