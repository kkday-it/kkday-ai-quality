<script setup lang="ts">
/**
 * 判決系統 KPI 總覽（landing）——接真後端 `attribution_overview`（source=undefined＝縱覽全部來源）。
 *
 * 呈現跨全部來源的即時歸因聚合：核心指標（進線 / 已判 / 問題比 / 自動採信率 / 待人工）+ 傾向 / L1 域 /
 * 信心分層 三分布。**棄原業務目標敘事 mock**（其北極星 / loop / 引擎指標不存在於資料、無法接真）。
 * 詳細 per-source、日期趨勢、L1→L2/L3 下鑽走「歸因縱覽」頁；此處為高階 landing 概覽，復用同一
 * `useAttributionDashboard` 真實資料源，不重造。
 */
import VChart from 'vue-echarts';
import { use } from 'echarts/core';
import { BarChart, PieChart } from 'echarts/charts';
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { IconRefresh } from '@arco-design/web-vue/es/icon';
import { CardSection, StateGuard } from '@/components';
import { KpiCard } from '@/features/judge/components';
import { useAttributionDashboard } from '@/features/judge/composables';

use([PieChart, BarChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer]);

// source=undefined → 跨全部來源聚合（判決系統整體 KPI）；landing 取整體、不帶日期/粒度篩選。
const { loading, error, hasData, kpi, polarityDonut, l1Bar, tierDonut, reload } =
  useAttributionDashboard(() => undefined);
</script>

<template>
  <div class="mx-auto max-w-[1320px]">
    <header class="mb-5 flex flex-wrap items-end justify-between gap-2">
      <div>
        <h1 class="m-0 text-xl font-semibold text-[#1d2129]">判決系統總覽</h1>
        <p class="mt-1 text-sm text-[#86909c]">跨全部來源的即時歸因聚合 · 詳細 per-source / 趨勢 / 下鑽見「歸因縱覽」</p>
      </div>
      <a-button size="small" :loading="loading" @click="reload">
        <template #icon><icon-refresh /></template>
        重新整理
      </a-button>
    </header>

    <StateGuard
      :loading="loading"
      :error="error"
      :empty="!hasData"
      empty-text="尚無歸因資料，請先到「歸因列表」進行初判歸因"
    >
      <div v-if="kpi" class="flex flex-col gap-4">
        <CardSection title="核心指標" hint="整體進線結構：進線量、歸因進度與問題 / 自動採信比率">
          <div class="grid grid-cols-2 gap-4 md:grid-cols-5">
            <KpiCard label="總進線" :value="kpi.total" subtext="全部錄入標的" />
            <KpiCard label="已歸因" :value="kpi.judged" subtext="已完成初判歸因" />
            <KpiCard label="問題占比" :value="kpi.problemPct" unit="%" subtext="負向 / 已判" />
            <KpiCard label="自動採信率" :value="kpi.autoPct" unit="%" subtext="auto_accept / 已判" />
            <KpiCard label="待人工" :value="kpi.needsReview" subtext="低信心需複核" />
          </div>
        </CardSection>

        <a-row :gutter="[16, 16]" align="stretch">
          <a-col :span="8">
            <CardSection title="傾向分布" hint="正向 / 負向 / 中性 / 傾向不明 占比">
              <v-chart :option="polarityDonut" class="h-[320px]" autoresize />
            </CardSection>
          </a-col>
          <a-col :span="8">
            <CardSection title="L1 歸因域分布" hint="負向問題的歸因域分布（詳細下鑽見歸因縱覽）">
              <v-chart :option="l1Bar" class="h-[320px]" autoresize />
            </CardSection>
          </a-col>
          <a-col :span="8">
            <CardSection title="信心分層" hint="自動採信 / 陪審 / 待人工 三段分流">
              <v-chart :option="tierDonut" class="h-[320px]" autoresize />
            </CardSection>
          </a-col>
        </a-row>
      </div>
    </StateGuard>
  </div>
</template>
