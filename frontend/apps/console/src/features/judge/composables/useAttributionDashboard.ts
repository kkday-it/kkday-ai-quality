import { ref, computed, watch, toValue, type MaybeRefOrGetter } from 'vue';
import { getAttributionOverview, getAttributionBreakdown } from '@/api';
import { useVerticalFilterStore } from '@/stores';
import { buildDonutOption, buildBarOption, buildTrendOption } from '@/features/overview/utils';
import {
  buildAttrFunnelOption,
  type AttributionOverview,
  type AttributionBreakdown,
  type CountItem,
} from '../utils';
import { POLARITY_LABELS, TIER_LABELS } from '../constants';

/** 傾向語義色（對齊 AttributionList 的 POLARITY_COLOR 語義）。 */
const POL_COLOR: Record<string, string> = {
  positive: '#00b42a',
  negative: '#f53f3f',
  neutral: '#86909c',
  unknown: '#ff7d00',
};

/** 信心分層三段（語義色固定；label 走 SSOT）。 */
const TIERS = [
  { key: 'auto_accept', color: '#00b42a' },
  { key: 'jury', color: '#ff7d00' },
  { key: 'needs_review', color: '#f53f3f' },
] as const;

/** 商品內容 L1 域 code（config/ai_judge/domains.json items[].code，圈號① 商品內容）——優先關注佔比用。 */
const CONTENT_DOMAIN_CODE = 'content';

/** useAttributionDashboard 的響應式查詢輸入（日期區間 + 趨勢粒度；皆可 ref / getter / 純值）。 */
export interface AttrDashboardQuery {
  dateFrom?: MaybeRefOrGetter<string | undefined>;
  dateTo?: MaybeRefOrGetter<string | undefined>;
  granularity?: MaybeRefOrGetter<string | undefined>;
}

/**
 * 歸因縱覽儀表板的資料與圖表邏輯（縱覽 / 商品評論 / 售前售後進線 三檢視共用）。
 *
 * 把「載入聚合 + L1 下鑽 + 各 ECharts option 計算」自頁面下沉為 composable，讓多檢視只需綁定
 * 不同 source 即可各自取真實資料、共享同一套圖表建構邏輯，不平行造第二套。
 *
 * @param source 目標來源；傳 `undefined` 代表全部來源（縱覽）。可為 ref / getter / 純值，
 *   內部以 `toValue` 解包並 watch，切換檢視（source 變更）時自動重載並清空下鑽狀態。
 * @param query 日期區間 + 趨勢粒度（皆響應式；任一變更即自動重載）。
 * @returns 載入三態、KPI 與各圖表 option（computed）、L1 下鑽狀態與操作、`reload` 手動重整。
 */
export function useAttributionDashboard(
  source: MaybeRefOrGetter<string | undefined>,
  query: AttrDashboardQuery = {},
) {
  // 全局商品垂直分類篩選（工具列複選 = 規則配置頁 = 縱覽，SSOT，控制整個 AI 法官總數）。
  const verticalFilter = useVerticalFilterStore();
  // 確保選項已載入（activeGroups「全選＝不篩選」判定需 options）；首次亦補成全選。
  verticalFilter.loadOptions();
  const effVerticals = () =>
    verticalFilter.activeGroups.length ? [...verticalFilter.activeGroups] : undefined;
  /** 縱覽工具列可選分類＝規則配置頁設定的選項池。 */
  const verticalOptions = computed(() => verticalFilter.toolbarOptions);
  /** 縱覽工具列篩選選中（與歸因列表同一份 SSOT，改任一處兩頁同步）。 */
  const verticalGroups = computed(() => verticalFilter.filter);
  /** 複選變更：寫回全局 store（剩 1 不可移除由 setFilter 守衛）→ watch activeGroups 觸發縱覽重載。 */
  const onVerticalChange = (v: unknown) =>
    verticalFilter.setFilter(Array.isArray(v) ? (v as string[]) : []);

  const data = ref<AttributionOverview | null>(null);
  const loading = ref(true);
  const error = ref('');

  // ── L1 下鑽（懶載）──
  const drillL1 = ref<{ code: string; label: string } | null>(null);
  const breakdown = ref<AttributionBreakdown | null>(null);
  const drillLoading = ref(false);

  /** 載入縱覽聚合（切來源 / 重新整理共用）；同時清空下鑽狀態。 */
  const reload = async () => {
    loading.value = true;
    error.value = '';
    drillL1.value = null;
    breakdown.value = null;
    try {
      data.value = (await getAttributionOverview({
        source: toValue(source),
        dateFrom: toValue(query.dateFrom),
        dateTo: toValue(query.dateTo),
        granularity: toValue(query.granularity),
        productVerticals: effVerticals(),
      })) as AttributionOverview;
    } catch (e: unknown) {
      error.value = '載入失敗：' + (e instanceof Error ? e.message : String(e));
    } finally {
      loading.value = false;
    }
  };

  /** 點 L1 長條 → 載該域 L2/L3 細項分布（以當前 source 過濾）。 */
  const openDrill = async (code: string, label: string) => {
    drillL1.value = { code, label };
    drillLoading.value = true;
    breakdown.value = null;
    try {
      breakdown.value = (await getAttributionBreakdown(code, {
        source: toValue(source),
        dateFrom: toValue(query.dateFrom),
        dateTo: toValue(query.dateTo),
        productVerticals: effVerticals(),
      })) as AttributionBreakdown;
    } catch (e: unknown) {
      error.value = '下鑽失敗：' + (e instanceof Error ? e.message : String(e));
    } finally {
      drillLoading.value = false;
    }
  };

  /** ECharts 長條點擊：以 category 名反查 L1 code（by_l1 的 label 唯一）。 */
  const onL1Click = (p: { name: string }) => {
    const hit = data.value?.by_l1.find((d) => d.label === p.name);
    if (hit) openDrill(hit.code, hit.label);
  };

  // source（檢視）/ 日期區間 / 粒度 任一變更即自動重載；immediate 取代 onMounted 首載
  watch(
    () => [
      toValue(source),
      toValue(query.dateFrom),
      toValue(query.dateTo),
      toValue(query.granularity),
      verticalFilter.activeGroups, // 全局垂直分類變更 → 縱覽同步重載
    ],
    reload,
    { immediate: true },
  );

  const hasData = computed(() => !!data.value && data.value.total_intake > 0);

  /** KPI：問題占比 / 自動採信率以「已判」為分母（未判不計入比率語義）。 */
  const kpi = computed(() => {
    const d = data.value;
    if (!d) return null;
    const neg = d.by_polarity.find((p) => p.polarity === 'negative')?.n ?? 0;
    const j = d.judged || 0;
    return {
      total: d.total_intake,
      judged: d.judged,
      problemPct: j ? Math.round((neg / j) * 100) : 0,
      autoPct: j ? Math.round((d.by_tier.auto_accept / j) * 100) : 0,
      needsReview: d.by_tier.needs_review,
    };
  });

  /** 排名長條：desc 清單反轉，使最大值顯示在頂端（ECharts category index 0 在底部）。 */
  const rankBar = (title: string, items: CountItem[], color: string) =>
    buildBarOption({
      title,
      unit: '筆',
      items: items.map((it) => ({ name: it.label, value: it.n, color })).reverse(),
    });

  const polarityDonut = computed(() =>
    buildDonutOption({
      title: '傾向分布',
      unit: '筆',
      items: (data.value?.by_polarity ?? []).map((p) => ({
        name: p.label,
        value: p.n,
        color: POL_COLOR[p.polarity] ?? '#86909c',
      })),
    }),
  );

  /**
   * 商品內容佔比（優先關注指標）：L1 域 content 佔全部歸因域比重，2 片甜甜圈（商品內容 vs 其他域）。
   * 純由 by_l1 前端計算；各檢視（來源）各自呈現該來源下商品內容問題的佔比。
   */
  const contentRatioDonut = computed(() => {
    const items = data.value?.by_l1 ?? [];
    const total = items.reduce((sum, it) => sum + it.n, 0);
    const content = items.find((it) => it.code === CONTENT_DOMAIN_CODE)?.n ?? 0;
    return buildDonutOption({
      title: '商品內容佔比',
      unit: '筆',
      items: [
        { name: '商品內容', value: content, color: '#165dff' },
        { name: '其他歸因域', value: Math.max(total - content, 0), color: '#e5e6eb' },
      ],
    });
  });

  /** 星等分布（僅商品評論類來源有 score 資料；低星紅、中性灰、高星綠）。 */
  const scoreBar = computed(() =>
    buildBarOption({
      title: '星等分布',
      unit: '筆',
      items: (data.value?.by_score ?? []).map((s) => ({
        name: `${s.score} 星`,
        value: s.n,
        color: s.score >= 4 ? '#00b42a' : s.score === 3 ? '#86909c' : '#f53f3f',
      })),
    }),
  );

  const funnel = computed(() => {
    const d = data.value;
    if (!d) return buildAttrFunnelOption([]);
    const neg = d.by_polarity.find((p) => p.polarity === 'negative')?.n ?? 0;
    return buildAttrFunnelOption([
      { name: '反饋', value: d.total_intake },
      { name: '已判', value: d.judged },
      { name: POLARITY_LABELS.negative, value: neg },
      { name: '已歸因', value: d.attributed },
    ]);
  });

  const l1Bar = computed(() => rankBar('L1 歸因域分布', data.value?.by_l1 ?? [], '#165dff'));
  const l2Bar = computed(() => rankBar('L2 細項', breakdown.value?.by_l2 ?? [], '#4080ff'));
  const l3Bar = computed(() => rankBar('L3 細項', breakdown.value?.by_l3 ?? [], '#6aa1ff'));

  const tierDonut = computed(() =>
    buildDonutOption({
      title: '信心分層',
      unit: '筆',
      items: TIERS.map((t) => ({
        name: TIER_LABELS[t.key] || t.key,
        value: data.value?.by_tier[t.key] ?? 0,
        color: t.color,
      })),
    }),
  );

  const trend = computed(() =>
    buildTrendOption({
      title: '問題量趨勢',
      unit: '筆',
      months: data.value?.trend.months ?? [],
      series: [
        { name: '已判', data: data.value?.trend.judged ?? [] },
        { name: POLARITY_LABELS.negative, data: data.value?.trend.negative ?? [] },
      ],
    }),
  );

  return {
    loading,
    error,
    hasData,
    kpi,
    // 下鑽狀態與操作
    drillL1,
    breakdown,
    drillLoading,
    openDrill,
    onL1Click,
    reload,
    // 全局垂直分類篩選（縱覽工具列）
    verticalOptions,
    verticalGroups,
    onVerticalChange,
    // 圖表 option
    polarityDonut,
    contentRatioDonut,
    scoreBar,
    funnel,
    l1Bar,
    l2Bar,
    l3Bar,
    tierDonut,
    trend,
  };
}
