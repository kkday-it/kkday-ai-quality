<script setup lang="ts">
/**
 * 歸因概覽（Attribution Overview）多檢視數據展示頁。
 *
 * 頁內次級 tab：縱覽（全部來源）+ 每個反饋來源各一專屬概覽頁（順序＝sources.json）。
 * 各檢視共用 useAttributionDashboard——綁定不同 source 取真實聚合資料，切換即自動重載。
 * 圖表按 source 差異呈現：星等分布僅有星等欄的來源（SCORE_SOURCES）顯示。
 * 支援導出當前檢視為 PDF 報表（複用 reportPdf，抓頁內 data-report-block 面板）。
 *
 * Phase 2（後端待補，暫不呈現以免造假）：初判分布（attributions 無 verdict 欄）、
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
import { Message } from '@arco-design/web-vue';
import { IconRefresh, IconDownload } from '@arco-design/web-vue/es/icon';
import { StateGuard, CardSection, KpiCard } from '@/components';
import { SOURCES } from '../constants';
import { useAttributionDashboard } from '../composables';
import { exportBlocksToPdf } from '../utils';

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

/** 有星等欄的來源（product_reviews=rec_scores / freshdesk=st_survey_rating / app_feedback=score）→ 才顯示星等分布。 */
const SCORE_SOURCES = new Set(['product_reviews', 'freshdesk_tickets', 'app_feedback']);

/** 單一檢視：key（＝source code 或 'overview'）/ source（undefined＝縱覽全部）/ 顯示名 / 有無星等。 */
interface DashView {
  key: string;
  source: string | undefined;
  label: string;
  showScore: boolean;
}

/**
 * 檢視目錄：縱覽（全部來源）+ 每個反饋來源各一專屬概覽頁。
 * 順序與 tab 標籤皆衍生自 config/global/sources.json（SSOT＝SOURCES）——新增/調整來源只改該檔，
 * 本頁自動同步，不平行維護第二份順序；showScore 決定該檢視是否顯示星等分布。
 */
const VIEWS: DashView[] = [
  { key: 'overview', source: undefined, label: '整體概覽', showScore: false },
  ...SOURCES.map((s) => ({
    key: s.value,
    source: s.value,
    label: s.label,
    showScore: SCORE_SOURCES.has(s.value),
  })),
];

const view = ref<string>('overview');
const active = computed(() => VIEWS.find((v) => v.key === view.value) ?? VIEWS[0]);

// 歸因佔比圖表切換（圓餅＝占比視角 / 長條＝排名視角）
const shareChart = ref<'donut' | 'bar'>('donut');

// 日期區間（a-range-picker，'YYYY-MM-DD' 陣列）+ 趨勢粒度（年/月/日），驅動所有面板重載
const dateRange = ref<string[]>([]);
const granularity = ref('month');
const granLabel = computed(
  () => ({ year: '年', month: '月', day: '日' })[granularity.value] ?? '月',
);

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
  verticalOptions,
  verticalGroups,
  onVerticalChange,
  modelFilter,
  modelOptions,
  modelFiltered,
  attributionShareDonut,
  attributionShareBar,
  scoreBar,
  funnel,
  l1Bar,
  l2Bar,
  tierDonut,
  trend,
  contentL2Bar,
} = useAttributionDashboard(() => active.value.source, {
  dateFrom: () => dateRange.value?.[0],
  dateTo: () => dateRange.value?.[1],
  granularity,
});

// ── 導出報表（PDF）：抓當前頁已渲染的各面板（data-report-block）依序成報，複用 reportPdf ──
const reportRef = ref<HTMLElement | null>(null);
const exporting = ref(false);

/**
 * 導出當前概覽頁為 PDF：收集頁內標記 data-report-block 的面板卡片依視覺順序成報。
 * 只抓「當前已渲染」區塊（如下鑽面板未展開就不含），報告頭帶當前檢視 / 篩選 / 粒度描述。
 */
const onExport = async () => {
  const root = reportRef.value;
  if (!root) return;
  const blocks = Array.from(root.querySelectorAll<HTMLElement>('[data-report-block]'));
  if (!blocks.length) return;
  exporting.value = true;
  try {
    const now = new Date();
    const stamp = now.toLocaleString('zh-TW', { hour12: false });
    const ymd = now.toISOString().slice(0, 10);
    // 當前篩選描述（供報告頭）：檢視 / 垂直分類（非全選才列）/ 日期區間 / 趨勢粒度
    const filters: string[] = [`檢視：${active.value.label}`];
    if (verticalGroups.value.length && verticalGroups.value.length < verticalOptions.value.length)
      filters.push(`垂直分類：${verticalGroups.value.join('、')}`);
    if (dateRange.value?.[0] && dateRange.value?.[1])
      filters.push(`日期：${dateRange.value[0]} ~ ${dateRange.value[1]}`);
    filters.push(`趨勢粒度：${granLabel.value}`);
    const summary = kpi.value
      ? `總反饋 ${kpi.value.total} · 已歸因 ${kpi.value.judged} · 問題占比 ${kpi.value.problemPct}%`
      : undefined;
    await exportBlocksToPdf(
      blocks,
      { title: `歸因概覽 - ${active.value.label}`, generatedAt: stamp, filters, summary },
      `歸因概覽-${active.value.label}-${ymd}.pdf`,
    );
  } catch (e: unknown) {
    Message.error('導出失敗：' + (e instanceof Error ? e.message : String(e)));
  } finally {
    exporting.value = false;
  }
};
</script>

<template>
  <Teleport to="#page-toolbar">
    <div class="flex items-center gap-3">
      <a-radio-group v-model="view" type="button" size="small">
        <a-radio v-for="v in VIEWS" :key="v.key" :value="v.key">{{ v.label }}</a-radio>
      </a-radio-group>
      <!-- 商品垂直分類複選（與歸因列表同一 SSOT；嚴格限定縱覽數據範圍在所選分類內，含分類的來源才計入）-->
      <a-select
        :model-value="verticalGroups"
        multiple
        size="small"
        style="width: 200px"
        :max-tag-count="1"
        placeholder="商品垂直分類"
        :options="verticalOptions.map((g) => ({ value: g, label: g }))"
        @change="onVerticalChange"
      />
      <!-- 初判模型篩選（attributions.model 當前初判維度；套用後 KPI 卡揭露口徑，見下方 caption）-->
      <a-select
        v-model="modelFilter"
        multiple
        size="small"
        style="width: 200px"
        :max-tag-count="1"
        allow-clear
        placeholder="初判模型"
        :options="modelOptions"
      />
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
      <a-button
        size="small"
        type="outline"
        :loading="exporting"
        :disabled="!hasData"
        @click="onExport"
      >
        <template #icon><icon-download /></template>
        導出報表
      </a-button>
    </div>
  </Teleport>

  <StateGuard
    :loading="loading"
    :error="error"
    :empty="!hasData"
    empty-text="此來源尚無歸因資料，請先到「歸因列表」進行初判歸因"
  >
    <div v-if="kpi" ref="reportRef" class="flex flex-col gap-4">
      <!-- ── 核心指標 ── -->
      <CardSection
        data-report-block
        title="核心指標"
        hint="整體反饋結構：反饋量、歸因進度與問題比率"
      >
        <div class="grid grid-cols-2 gap-4 md:grid-cols-5">
          <KpiCard label="總反饋" :value="kpi.total" subtext="全部錄入標的" />
          <KpiCard
            :label="modelFiltered ? '已歸因（所選模型）' : '已歸因'"
            :value="kpi.judged"
            :subtext="modelFiltered ? '所選模型的初判覆蓋' : '已完成初判歸因'"
          />
          <KpiCard label="問題占比" :value="kpi.problemPct" unit="%" subtext="負向 / 已初判" />
          <KpiCard
            label="自動採信率"
            :value="kpi.autoPct"
            unit="%"
            subtext="auto_accept / 已初判"
          />
          <KpiCard label="待人工" :value="kpi.needsReview" subtext="低信心需複核" />
        </div>
        <!-- 初判模型篩選口徑揭露：attributions 為「當前初判」，每評論僅一個 model 值——
             與「總反饋」的差額含「未初判」與「被其他模型判過但未被選中」兩種情況，非皆未初判 -->
        <div v-if="modelFiltered" class="mt-2 text-xs text-[var(--color-text-3)]">
          已套用初判模型篩選：數字為「當前初判＝所選模型」的覆蓋；與「總反饋」的差額包含「未初判」與「由其他模型初判」兩種情況，非皆為未初判。
        </div>
      </CardSection>

      <!-- 商品內容細化：L2 面向問題分布；長條即負向筆數 + tooltip 全維度 -->
      <CardSection
        data-report-block
        title="商品內容細化"
        hint="商品內容底下的 L2 面向問題分布"
        desc="商品內容（L1）底下的 L2 面向問題分布（僅負向才歸類，長條即負向筆數）。滑鼠移入看完整維度（筆數 / 占比 / 平均信心 / 自動採信率）。"
      >
        <div class="mb-1 text-xs text-gray-500">L2 面向</div>
        <v-chart :option="contentL2Bar" class="h-[360px]" autoresize />
      </CardSection>

      <!-- 歸因佔比（全部 L1 域組成·可切圓餅/長條）＋ 問題量趨勢 -->
      <a-row :gutter="[16, 16]" align="stretch">
        <a-col :span="12">
          <CardSection
            data-report-block
            title="歸因佔比"
            desc="全部 L1 歸因域的問題組成佔比（僅負向才歸類，故＝負向問題的域分布）。可切換圓餅（占比視角）／長條（排名視角）；下方「L1 歸因域分布」可點長條下鑽 L2。"
          >
            <template #extra>
              <a-radio-group v-model="shareChart" type="button" size="mini">
                <a-radio value="donut">圓餅</a-radio>
                <a-radio value="bar">長條</a-radio>
              </a-radio-group>
            </template>
            <v-chart
              :option="shareChart === 'donut' ? attributionShareDonut : attributionShareBar"
              class="h-[320px]"
              autoresize
            />
          </CardSection>
        </a-col>
        <a-col :span="12">
          <CardSection
            data-report-block
            :title="`問題量趨勢（${granLabel}）`"
            hint="依評論時間聚合 · 已初判 vs 負向問題量"
          >
            <v-chart :option="trend" class="h-[320px]" autoresize />
          </CardSection>
        </a-col>
      </a-row>

      <!-- ── 問題歸因 ── -->
      <a-row :gutter="[16, 16]" align="stretch">
        <a-col :span="12">
          <CardSection
            data-report-block
            title="歸因漏斗"
            hint="反饋 → 已初判 → 負向 → 已歸因，逐級收斂"
          >
            <v-chart :option="funnel" class="h-[320px]" autoresize />
          </CardSection>
        </a-col>
        <a-col :span="12">
          <CardSection
            data-report-block
            title="L1 歸因域分布"
            hint="負向問題的六大歸因域 · 點長條下鑽 L2"
          >
            <v-chart :option="l1Bar" class="h-[320px]" autoresize @click="onL1Click" />
          </CardSection>
        </a-col>
      </a-row>

      <CardSection
        v-if="drillL1"
        data-report-block
        :title="`下鑽：${drillL1.label}（L2 面向）`"
        hint="該歸因域下的 L2 面向分布"
      >
        <template #extra>
          <a-link @click="drillL1 = null">收合</a-link>
        </template>
        <a-spin :loading="drillLoading" class="block w-full">
          <a-empty
            v-if="!drillLoading && !breakdown?.by_l2.length"
            description="該域暫無 L2 面向資料"
          />
          <div v-else>
            <div class="mb-1 text-xs text-gray-500">L2 面向</div>
            <v-chart :option="l2Bar" class="h-[300px]" autoresize />
          </div>
        </a-spin>
      </CardSection>

      <!-- 星等分布（僅商品評論類有 score）＋ 信心分層；無星等時信心占整寬 -->
      <a-row :gutter="[16, 16]" align="stretch">
        <a-col v-if="active.showScore" :span="12">
          <CardSection data-report-block title="星等分布" hint="全量反饋星等（高星綠 · 低星紅）">
            <v-chart :option="scoreBar" class="h-[320px]" autoresize />
          </CardSection>
        </a-col>
        <a-col :span="active.showScore ? 12 : 24">
          <CardSection data-report-block title="信心分層" hint="自動採信 / 陪審 / 待人工 三段分流">
            <v-chart :option="tierDonut" class="h-[320px]" autoresize />
          </CardSection>
        </a-col>
      </a-row>
    </div>
  </StateGuard>
</template>
