<script setup lang="ts">
/**
 * 歸因縱覽（Attribution Overview）數據展示頁。
 *
 * 把「歸因列表」的逐筆資料聚合成儀表板：上半「全量品質健康」（KPI + 傾向 + 星等）、
 * 下半「問題歸因」（漏斗 + L1 七域 + L2/L3 下鑽 + 判決 + 信心分層）+ 月趨勢。
 * 資料一次取自後端聚合端點（避免前端全量 fetch）；通用圖表複用 overview 既有 builder，
 * 只有 count 語義漏斗用本 feature 的 buildAttrFunnelOption。
 */
import { ref, computed, onMounted } from 'vue';
import VChart from 'vue-echarts';
import { use } from 'echarts/core';
import { PieChart, BarChart, LineChart, FunnelChart } from 'echarts/charts';
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  MarkLineComponent,
} from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { IconRefresh } from '@arco-design/web-vue/es/icon';
import { StateGuard, CardSection } from '@/components';
import { KpiCard } from '../components';
import { getAttributionOverview, getAttributionBreakdown } from '@/api';
import { buildDonutOption, buildBarOption, buildTrendOption } from '@/features/overview/utils';
import {
  buildAttrFunnelOption,
  type AttributionOverview,
  type AttributionBreakdown,
  type CountItem,
} from '../utils';
import { SOURCES } from '../constants';

use([
  PieChart,
  BarChart,
  LineChart,
  FunnelChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  MarkLineComponent,
  CanvasRenderer,
]);

const SOURCE_OPTS = SOURCES.map((s) => ({ value: s.value, label: s.label }));
/** 信心分層 code → 繁中（純顯示；未知 code 回退原值）。 */
const tierLabel: Record<string, string> = {
  auto_accept: '自動採信',
  jury: 'jury 覆核',
  needs_review: '待人工',
  hold: 'HOLD',
};

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

const source = ref('product_reviews');
const data = ref<AttributionOverview | null>(null);
const loading = ref(true);
const error = ref('');

// ── L1 下鑽（懶載）──
const drillL1 = ref<{ code: string; label: string } | null>(null);
const breakdown = ref<AttributionBreakdown | null>(null);
const drillLoading = ref(false);

/** 載入縱覽聚合（切來源 / 重新整理共用）；同時清空下鑽狀態。 */
const load = async () => {
  loading.value = true;
  error.value = '';
  drillL1.value = null;
  breakdown.value = null;
  try {
    data.value = (await getAttributionOverview(source.value)) as AttributionOverview;
  } catch (e: unknown) {
    error.value = '載入失敗：' + (e instanceof Error ? e.message : String(e));
  } finally {
    loading.value = false;
  }
};

/** 點 L1 長條 → 載該域 L2/L3 細項分布。 */
const openDrill = async (code: string, label: string) => {
  drillL1.value = { code, label };
  drillLoading.value = true;
  breakdown.value = null;
  try {
    breakdown.value = (await getAttributionBreakdown(code, source.value)) as AttributionBreakdown;
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

onMounted(load);

const hasData = computed(() => !!data.value && data.value.total_intake > 0);

/** KPI：問題占比/自動採信率以「已判」為分母（未判不計入比率語義）。 */
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

const scoreBar = computed(() =>
  buildBarOption({
    title: '星等分布',
    unit: '筆',
    // 1→5 升序（5 星在頂部），低星紅、中性灰、高星綠
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
    { name: '進線', value: d.total_intake },
    { name: '已判', value: d.judged },
    { name: '負向', value: neg },
    { name: '已歸因', value: d.attributed },
  ]);
});

const l1Bar = computed(() => rankBar('L1 歸因域分布', data.value?.by_l1 ?? [], '#165dff'));
const l2Bar = computed(() => rankBar('L2 細項', breakdown.value?.by_l2 ?? [], '#4080ff'));
const l3Bar = computed(() => rankBar('L3 細項', breakdown.value?.by_l3 ?? [], '#6aa1ff'));

const verdictBar = computed(() =>
  rankBar(
    '判決分布',
    (data.value?.by_verdict ?? []).map((v) => ({ code: v.verdict, label: v.label, n: v.n })),
    '#165dff',
  ),
);

const tierDonut = computed(() =>
  buildDonutOption({
    title: '信心分層',
    unit: '筆',
    items: TIERS.map((t) => ({
      name: tierLabel[t.key] || t.key,
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
      { name: '負向', data: data.value?.trend.negative ?? [] },
    ],
  }),
);
</script>

<template>
  <Teleport to="#page-toolbar">
    <div class="flex items-center gap-3">
      <span class="text-sm text-gray-500">來源</span>
      <a-select v-model="source" style="width: 160px" :options="SOURCE_OPTS" @change="load" />
      <a-button size="small" :loading="loading" @click="load">
        <template #icon><icon-refresh /></template>
        重新整理
      </a-button>
    </div>
  </Teleport>

  <StateGuard
    :loading="loading"
    :error="error"
    :empty="!hasData"
    empty-text="尚無歸因資料，請先到「歸因列表」進行初判歸因"
  >
    <div v-if="kpi" class="flex flex-col gap-4">
      <!-- ── 區塊一：全量品質健康 ── -->
      <CardSection title="全量品質健康" hint="整體進線結構：傾向占比、星等分布與歸因進度">
        <div class="grid grid-cols-2 gap-4 md:grid-cols-5">
          <KpiCard label="總進線" :value="kpi.total" subtext="全部錄入標的" />
          <KpiCard label="已歸因" :value="kpi.judged" subtext="已完成初判歸因" />
          <KpiCard label="問題占比" :value="kpi.problemPct" unit="%" subtext="負向 / 已判" />
          <KpiCard label="自動採信率" :value="kpi.autoPct" unit="%" subtext="auto_accept / 已判" />
          <KpiCard label="待人工" :value="kpi.needsReview" subtext="低信心需複核" />
        </div>
      </CardSection>

      <a-row :gutter="[16, 16]" align="stretch">
        <a-col :span="12">
          <CardSection title="傾向分布" hint="正向 / 負向 / 中性 / 數據不足 占比">
            <v-chart :option="polarityDonut" class="h-[300px]" autoresize />
          </CardSection>
        </a-col>
        <a-col :span="12">
          <CardSection title="星等分布" hint="全量進線星等（高星綠 · 低星紅）">
            <v-chart :option="scoreBar" class="h-[300px]" autoresize />
          </CardSection>
        </a-col>
      </a-row>

      <!-- ── 區塊二：問題歸因 ── -->
      <a-row :gutter="[16, 16]" align="stretch">
        <a-col :span="12">
          <CardSection title="歸因漏斗" hint="進線 → 已判 → 負向 → 已歸因，逐級收斂">
            <v-chart :option="funnel" class="h-[320px]" autoresize />
          </CardSection>
        </a-col>
        <a-col :span="12">
          <CardSection title="L1 歸因域分布" hint="負向問題的七大歸因域 · 點長條下鑽 L2/L3">
            <v-chart :option="l1Bar" class="h-[320px]" autoresize @click="onL1Click" />
          </CardSection>
        </a-col>
      </a-row>

      <CardSection
        v-if="drillL1"
        :title="`下鑽：${drillL1.label}（L2 / L3 細項）`"
        hint="該歸因域下的二、三層細項分布"
      >
        <template #extra>
          <a-link @click="drillL1 = null">收合</a-link>
        </template>
        <a-spin :loading="drillLoading" class="block w-full">
          <a-empty
            v-if="!drillLoading && !breakdown?.by_l2.length && !breakdown?.by_l3.length"
            description="該域暫無 L2/L3 細項資料"
          />
          <a-row v-else :gutter="[16, 16]" align="stretch">
            <a-col :span="12">
              <div class="mb-1 text-xs text-gray-500">L2 面向</div>
              <v-chart :option="l2Bar" class="h-[300px]" autoresize />
            </a-col>
            <a-col :span="12">
              <div class="mb-1 text-xs text-gray-500">L3 細項</div>
              <v-chart :option="l3Bar" class="h-[300px]" autoresize />
            </a-col>
          </a-row>
        </a-spin>
      </CardSection>

      <a-row :gutter="[16, 16]" align="stretch">
        <a-col :span="12">
          <CardSection title="判決分布" hint="9 種 verdict 的判決結果分布">
            <v-chart :option="verdictBar" class="h-[320px]" autoresize />
          </CardSection>
        </a-col>
        <a-col :span="12">
          <CardSection title="信心分層" hint="自動採信 / 陪審 / 待人工 三段分流">
            <v-chart :option="tierDonut" class="h-[320px]" autoresize />
          </CardSection>
        </a-col>
      </a-row>

      <CardSection title="問題量趨勢（月）" hint="依評論時間聚合 · 已判 vs 負向問題量">
        <v-chart :option="trend" class="h-[320px]" autoresize />
      </CardSection>
    </div>
  </StateGuard>
</template>
