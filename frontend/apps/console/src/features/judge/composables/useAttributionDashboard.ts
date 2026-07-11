import { ref, computed, watch, toValue, type MaybeRefOrGetter } from 'vue';
import { getAttributionOverview, getAttributionBreakdown, getJudgmentModels } from '@/api';
import { useVerticalFilterStore } from '@/stores';
import { buildDonutOption, buildBarOption, buildTrendOption } from '@/shared/charts';
import {
  buildAttrFunnelOption,
  buildContentBarOption,
  type AttributionOverview,
  type AttributionBreakdown,
  type BreakdownRow,
  type CountItem,
} from '../utils';
import { POLARITY_LABELS, TIER_LABELS } from '../constants';

/** 信心分層三段（語義色固定；label 走 SSOT）。 */
const TIERS = [
  { key: 'auto_accept', color: '#00b42a' },
  { key: 'jury', color: '#ff7d00' },
  { key: 'needs_review', color: '#f53f3f' },
] as const;

/** L1 歸因域配色（依 by_l1 顯示序循環取色；固定序保證圓餅 / 長條同域同色）。 */
const L1_PALETTE = [
  '#165dff',
  '#00b42a',
  '#ff7d00',
  '#f53f3f',
  '#722ed1',
  '#14c9c9',
  '#eb0aa4',
  '#7bc616',
  '#f7ba1e',
  '#3491fa',
];

/** useAttributionDashboard 的響應式查詢輸入（日期區間 + 趨勢粒度；皆可 ref / getter / 純值）。 */
export interface AttrDashboardQuery {
  dateFrom?: MaybeRefOrGetter<string | undefined>;
  dateTo?: MaybeRefOrGetter<string | undefined>;
  granularity?: MaybeRefOrGetter<string | undefined>;
}

/**
 * 歸因概覽儀表板的資料與圖表邏輯（縱覽 + 各反饋來源專屬概覽，多檢視共用）。
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

  // 判決模型篩選（工具列多選；judgments.model IN——當前判決維度）。空＝不篩選。
  // ⚠️ 語義：套用後 judged 變「所選模型的判決覆蓋數」，與總反饋差額含「他模型判過」非皆未判
  //（KPI 文案由頁面依 modelFiltered 揭露）。
  const modelFilter = ref<string[]>([]);
  /** 判決模型選項（歷來實際判過的模型；載一次）；失敗回空不阻斷概覽。 */
  const modelOptions = ref<{ value: string; label: string }[]>([]);
  getJudgmentModels()
    .then((models) => {
      modelOptions.value = models.map((m) => ({
        value: m,
        label: m === 'stub' ? 'stub（測試假判，非真實模型）' : m,
      }));
    })
    .catch(() => {
      modelOptions.value = [];
    });
  /** 是否已套用判決模型篩選（KPI 文案揭露口徑用）。 */
  const modelFiltered = computed(() => modelFilter.value.length > 0);
  const effModel = () => (modelFilter.value.length ? [...modelFilter.value] : undefined);

  const data = ref<AttributionOverview | null>(null);
  const loading = ref(true);
  const error = ref('');

  // ── L1 下鑽（懶載）──
  const drillL1 = ref<{ code: string; label: string } | null>(null);
  const breakdown = ref<AttributionBreakdown | null>(null);
  const drillLoading = ref(false);
  // ── 商品內容細化（常載·各檢視都有）：l1='content' 的 L2/L3 多指標，供左 L2 / 右 L3 互動圖 ──
  const contentBreakdown = ref<AttributionBreakdown | null>(null);
  // 左側點選的 L2 面向（null＝未載入 / 無資料）；切檢視重載時預設回筆數最多的 L2。
  const selectedL2 = ref<{ code: string; label: string } | null>(null);

  /** 載入縱覽聚合（切來源 / 重新整理共用）；同時清空下鑽狀態、常載商品內容細化。 */
  const reload = async () => {
    loading.value = true;
    error.value = '';
    drillL1.value = null;
    breakdown.value = null;
    const q = {
      source: toValue(source),
      dateFrom: toValue(query.dateFrom),
      dateTo: toValue(query.dateTo),
      productVerticals: effVerticals(),
      model: effModel(),
    };
    try {
      data.value = (await getAttributionOverview({
        ...q,
        granularity: toValue(query.granularity),
      })) as AttributionOverview;
      // 商品內容細化圖所需（失敗不阻斷縱覽主體）；預設選中筆數最多的 L2 讓右側 L3 有初值。
      contentBreakdown.value = (await getAttributionBreakdown(
        'content',
        q,
      )) as AttributionBreakdown;
      const top = contentBreakdown.value?.by_l2?.[0];
      selectedL2.value = top ? { code: top.code, label: top.label } : null;
    } catch (e: unknown) {
      error.value = '載入失敗：' + (e instanceof Error ? e.message : String(e));
    } finally {
      loading.value = false;
    }
  };

  /** BreakdownRow → ContentBarItem：帶全維度，占比以傳入 total 為分母（同層總數，呼叫端決定）。 */
  const toContentItems = (rows: BreakdownRow[]) => {
    const total = rows.reduce((s, r) => s + r.n, 0);
    return rows.map((r) => ({
      label: r.label,
      n: r.n,
      pct: total ? Math.round((r.n / total) * 1000) / 10 : 0,
      avgConf: r.avg_conf,
      autoRate: r.n ? Math.round((r.auto / r.n) * 1000) / 10 : 0,
    }));
  };

  /** 左側 L2 面向長條（占比＝該 L2 / 全 L2 總數）。 */
  const contentL2Bar = computed(() =>
    buildContentBarOption(toContentItems(contentBreakdown.value?.by_l2 ?? [])),
  );

  /** 右側 L3 細項長條：僅選中 L2 底下的 L3（占比＝該 L3 / 選中 L2 內 L3 總數，非全域）。 */
  const contentL3Bar = computed(() => {
    const code = selectedL2.value?.code;
    const rows = (contentBreakdown.value?.by_l3 ?? []).filter((r) => !code || r.l2_code === code);
    return buildContentBarOption(toContentItems(rows));
  });

  /** 點左側 L2 長條 → 以 category 名反查 L2 code，切換右側 L3（by_l2 label 唯一）。 */
  const onContentL2Click = (p: { name: string }) => {
    const hit = contentBreakdown.value?.by_l2.find((d) => d.label === p.name);
    if (hit) selectedL2.value = { code: hit.code, label: hit.label };
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
        model: effModel(),
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
      modelFilter.value, // 判決模型篩選變更 → 重載
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

  /**
   * 歸因佔比資料：全部 L1 歸因域各自佔比（僅負向才歸類，故＝負向問題的域組成）。
   * 依 by_l1 顯示序配固定色 → 圓餅 / 長條兩種呈現同域同色。各檢視（來源）各自呈現該來源下的域組成。
   */
  const attrShareItems = computed(() =>
    (data.value?.by_l1 ?? []).map((it, i) => ({
      name: it.label,
      value: it.n,
      color: L1_PALETTE[i % L1_PALETTE.length],
    })),
  );

  /** 歸因佔比——圓餅（占比視角，甜甜圈 + 右側可捲動 legend）。 */
  const attributionShareDonut = computed(() =>
    buildDonutOption({ title: '歸因佔比', unit: '筆', items: attrShareItems.value }),
  );

  /** 歸因佔比——長條（排名視角，最大值置頂）。與圓餅同資料，供圖表切換多維呈現。 */
  const attributionShareBar = computed(() =>
    buildBarOption({ title: '歸因佔比', unit: '筆', items: [...attrShareItems.value].reverse() }),
  );

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
    // 商品內容細化互動圖（常載·各檢視都有）：左 L2 可點 → 右 L3 即時更新
    selectedL2,
    contentL2Bar,
    contentL3Bar,
    onContentL2Click,
    // 全局垂直分類篩選（縱覽工具列）
    verticalOptions,
    verticalGroups,
    onVerticalChange,
    // 判決模型篩選（縱覽工具列；當前判決維度）
    modelFilter,
    modelOptions,
    modelFiltered,
    // 圖表 option
    attributionShareDonut,
    attributionShareBar,
    scoreBar,
    funnel,
    l1Bar,
    l2Bar,
    l3Bar,
    tierDonut,
    trend,
  };
}
