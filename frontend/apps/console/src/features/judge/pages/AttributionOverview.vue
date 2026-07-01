<script setup lang="ts">
/**
 * 歸因縱覽（Attribution Overview）多檢視數據展示頁。
 *
 * 頁內次級 tab 切三檢視：縱覽（全部來源）/ 商品評論（product_reviews）/ 售前售後進線（conversations）。
 * 三檢視共用 useAttributionDashboard——各自綁定 source 取真實聚合資料，切換即自動重載。
 * 圖表按 source 差異呈現：星等分布僅商品評論類來源有資料，故僅該檢視顯示。
 *
 * Phase 2（後端待補，暫不呈現以免造假）：判決分布（judgments 無 verdict 欄）、
 * 標籤情感（另一資料源）、售前售後進線的訂單/工單/供應商維度（需新聚合端點）。
 */
import { ref, computed } from 'vue';
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
import { useAttributionDashboard } from '../composables';

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

/**
 * 檢視目錄：每個檢視綁定固定 source（縱覽=全部即 undefined），並宣告該 source 有無星等資料。
 * source 值對齊 config/ai_judge/source_mapping.json 與 SOURCES 常數；
 * conversations（售前售後進線）無 score 映射 → showScore=false，不顯示星等分布。
 */
const VIEWS = [
  { key: 'overview', label: '縱覽', source: undefined, showScore: false },
  { key: 'reviews', label: '商品評論', source: 'product_reviews', showScore: true },
  { key: 'intake', label: '售前售後進線', source: 'conversations', showScore: false },
] as const;

const view = ref<(typeof VIEWS)[number]['key']>('overview');
const active = computed(() => VIEWS.find((v) => v.key === view.value)!);

// 日期區間（a-range-picker，'YYYY-MM-DD' 陣列）+ 趨勢粒度（年/月/日），驅動所有面板重載
const dateRange = ref<string[]>([]);
const granularity = ref('month');
const granLabel = computed(() => ({ year: '年', month: '月', day: '日' })[granularity.value] ?? '月');

// source / 日期 / 粒度 以 getter 傳入，任一變更即觸發 composable 內 watch 自動重載
const {
  loading,
  error,
  hasData,
  kpi,
  drillL1,
  breakdown,
  drillLoading,
  onL1Click,
  reload,
  polarityDonut,
  contentRatioDonut,
  scoreBar,
  funnel,
  l1Bar,
  l2Bar,
  l3Bar,
  tierDonut,
  trend,
} = useAttributionDashboard(() => active.value.source, {
  dateFrom: () => dateRange.value?.[0],
  dateTo: () => dateRange.value?.[1],
  granularity,
});
</script>

<template>
  <Teleport to="#page-toolbar">
    <div class="flex items-center gap-3">
      <a-radio-group v-model="view" type="button" size="small">
        <a-radio v-for="v in VIEWS" :key="v.key" :value="v.key">{{ v.label }}</a-radio>
      </a-radio-group>
      <a-range-picker
        v-model="dateRange"
        size="small"
        value-format="YYYY-MM-DD"
        style="width: 240px"
      />
      <a-radio-group v-model="granularity" type="button" size="small">
        <a-radio value="year">年</a-radio>
        <a-radio value="month">月</a-radio>
        <a-radio value="day">日</a-radio>
      </a-radio-group>
      <a-button size="small" :loading="loading" @click="reload">
        <template #icon><icon-refresh /></template>
        重新整理
      </a-button>
    </div>
  </Teleport>

  <StateGuard
    :loading="loading"
    :error="error"
    :empty="!hasData"
    empty-text="此來源尚無歸因資料，請先到「歸因列表」進行初判歸因"
  >
    <div v-if="kpi" class="flex flex-col gap-4">
      <!-- ── 核心指標 ── -->
      <CardSection title="核心指標" hint="整體進線結構：進線量、歸因進度與問題比率">
        <div class="grid grid-cols-2 gap-4 md:grid-cols-5">
          <KpiCard label="總進線" :value="kpi.total" subtext="全部錄入標的" />
          <KpiCard label="已歸因" :value="kpi.judged" subtext="已完成初判歸因" />
          <KpiCard label="問題占比" :value="kpi.problemPct" unit="%" subtext="負向 / 已判" />
          <KpiCard label="自動採信率" :value="kpi.autoPct" unit="%" subtext="auto_accept / 已判" />
          <KpiCard label="待人工" :value="kpi.needsReview" subtext="低信心需複核" />
        </div>
      </CardSection>

      <!-- 商品內容佔比（優先關注）＋ 傾向占比 ＋ 問題量趨勢 三欄並置 -->
      <a-row :gutter="[16, 16]" align="stretch">
        <a-col :span="8">
          <CardSection title="商品內容佔比" hint="商品內容問題佔全部歸因域比重 · 優先關注">
            <v-chart :option="contentRatioDonut" class="h-[320px]" autoresize />
          </CardSection>
        </a-col>
        <a-col :span="8">
          <CardSection title="傾向分布" hint="正向 / 負向 / 中性 / 數據不足 占比">
            <v-chart :option="polarityDonut" class="h-[320px]" autoresize />
          </CardSection>
        </a-col>
        <a-col :span="8">
          <CardSection :title="`問題量趨勢（${granLabel}）`" hint="依評論時間聚合 · 已判 vs 負向問題量">
            <v-chart :option="trend" class="h-[320px]" autoresize />
          </CardSection>
        </a-col>
      </a-row>

      <!-- ── 問題歸因 ── -->
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

      <!-- 星等分布（僅商品評論類有 score）＋ 信心分層；無星等時信心占整寬 -->
      <a-row :gutter="[16, 16]" align="stretch">
        <a-col v-if="active.showScore" :span="12">
          <CardSection title="星等分布" hint="全量進線星等（高星綠 · 低星紅）">
            <v-chart :option="scoreBar" class="h-[320px]" autoresize />
          </CardSection>
        </a-col>
        <a-col :span="active.showScore ? 12 : 24">
          <CardSection title="信心分層" hint="自動採信 / 陪審 / 待人工 三段分流">
            <v-chart :option="tierDonut" class="h-[320px]" autoresize />
          </CardSection>
        </a-col>
      </a-row>
    </div>
  </StateGuard>
</template>
