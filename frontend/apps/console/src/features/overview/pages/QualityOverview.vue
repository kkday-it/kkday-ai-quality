<script setup lang="ts">
/**
 * 質檢概覽：內容質量 & 閉環引擎整體視圖。
 *
 * 版面節奏（分區標題切大塊，每塊內維持規律 grid，避免一行 2/3 個的雜亂）：
 *   §1 核心指標 — 4-up 北極星卡（含 sparkline）
 *   §2 閉環引擎 — 全寬步驟流程 + 三大引擎卡 3-up
 *   §3 進線洞察 — 2-up：進線結構甜甜圈 + 落後指標趨勢
 *   §4 審品成效 — 2-up：審品攔截漏斗 + 領先指標趨勢
 *   §5 覆蓋與來源 — 2-up：商品類別覆蓋 + 指標資料來源
 *
 * 由 mock/overview.mock.json 單一驅動，接後端後換 /api/overview（形狀不變）。
 * AI 法官歸因僅是三大引擎之一，本頁定位為整個 AI 質檢的概覽。
 */
import { computed } from 'vue';
import { useRouter } from 'vue-router';
import VChart from 'vue-echarts';
import { use } from 'echarts/core';
import { LineChart, BarChart, PieChart, FunnelChart } from 'echarts/charts';
import { GridComponent, TooltipComponent, LegendComponent, MarkLineComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { CardSection } from '@/components';
import { SectionTitle, NorthStarCard, EngineCard, LoopFlow, SourceTable } from '../components';
import { buildTrendOption, buildCoverageOption, buildDonutOption, buildFunnelOption } from '../utils';
import type { OverviewData } from '../types';
import mockRaw from '../mock/overview.mock.json';

use([
  LineChart, BarChart, PieChart, FunnelChart,
  GridComponent, TooltipComponent, LegendComponent, MarkLineComponent, CanvasRenderer,
]);

// mock 為 demo 真相源；JSON 結構即 OverviewData，斷言以取得元件型別提示。
const data = mockRaw as unknown as OverviewData;
const router = useRouter();

const laggingOption = computed(() => buildTrendOption(data.laggingTrend));
const leadingOption = computed(() => buildTrendOption(data.leadingTrend));
const coverageOption = computed(() => buildCoverageOption(data.categoryCoverage));
const donutOption = computed(() => buildDonutOption(data.intakeBreakdown));
const funnelOption = computed(() => buildFunnelOption(data.reviewFunnel));

const goto = (route: string) => router.push(route);
</script>

<template>
  <div class="mx-auto flex max-w-[1320px] flex-col gap-7">
    <!-- 標題 -->
    <header class="flex flex-wrap items-end justify-between gap-2">
      <div>
        <h1 class="text-xl font-semibold text-[#1d2129]">{{ data.meta.title }}</h1>
        <p class="mt-1 text-sm text-[#86909c]">{{ data.meta.subtitle }} · {{ data.meta.period }}</p>
      </div>
      <a-tag color="orange" size="small" bordered>Demo · mock 資料</a-tag>
    </header>

    <!-- §1 核心指標 -->
    <section class="flex flex-col gap-3">
      <SectionTitle title="核心指標" subtitle="北極星・落後 + 領先" />
      <div class="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <NorthStarCard v-for="m in data.northStar" :key="m.key" :metric="m" />
      </div>
    </section>

    <!-- §2 閉環引擎 -->
    <section class="flex flex-col gap-3">
      <SectionTitle title="閉環引擎" subtitle="進線 → 歸因 → Action → 審品 → 供給 → 進線↓" />
      <CardSection
        title="閉環流程"
        desc="客訴與反饋進線，經 AI 法官歸因產出可執行 Action，驅動 AI 審品與內容撰寫修正供給內容，進而降低售後進線，形成持續收斂的閉環。"
      >
        <LoopFlow :steps="data.loop" :caption="data.meta.loopCaption" />
      </CardSection>
      <div class="grid grid-cols-1 gap-4 md:grid-cols-3">
        <EngineCard v-for="e in data.engines" :key="e.id" :engine="e" @navigate="goto" />
      </div>
    </section>

    <!-- §3 進線洞察 -->
    <section class="flex flex-col gap-3">
      <SectionTitle title="進線洞察" subtitle="售後進線結構與趨勢" />
      <div class="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <CardSection
          :title="data.intakeBreakdown.title"
          hint="占比 %"
          desc="售後進線依問題類別拆解；內容類為 AI 質檢的主攻面，目標壓低至 10% 以下。"
        >
          <v-chart :option="donutOption" autoresize class="h-[300px] w-full" />
        </CardSection>
        <CardSection :title="data.laggingTrend.title" hint="越低越好">
          <v-chart :option="laggingOption" autoresize class="h-[300px] w-full" />
        </CardSection>
      </div>
    </section>

    <!-- §4 審品成效 -->
    <section class="flex flex-col gap-3">
      <SectionTitle title="審品成效" subtitle="攔截漏斗與領先指標" />
      <div class="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <CardSection
          :title="data.reviewFunnel.title"
          hint="留存 %"
          desc="必填驗證 → AI 前審 → AI 後審 → 人工複核，逐級攔截高風險內容的留存比例。"
        >
          <v-chart :option="funnelOption" autoresize class="h-[300px] w-full" />
        </CardSection>
        <CardSection :title="data.leadingTrend.title" hint="越高越好">
          <v-chart :option="leadingOption" autoresize class="h-[300px] w-full" />
        </CardSection>
      </div>
    </section>

    <!-- §5 覆蓋與來源 -->
    <section class="flex flex-col gap-3">
      <SectionTitle title="覆蓋與資料來源" subtitle="商品類別 + 指標出處" />
      <div class="grid grid-cols-1 gap-5 lg:grid-cols-12">
        <CardSection
          title="商品類別覆蓋"
          hint="Tier2 / Tier3 · 依 P 級優先"
          desc="質檢覆蓋的商品類別範圍：Tour / Exp 為 P0 優先全展開，Tix P1、Charter P2 漸進納入。"
          class="lg:col-span-5"
        >
          <v-chart :option="coverageOption" autoresize class="h-[280px] w-full" />
        </CardSection>
        <CardSection title="指標資料來源 & 外部儀表板" class="lg:col-span-7">
          <SourceTable :rows="data.sources" />
        </CardSection>
      </div>
    </section>

    <p class="text-center text-xs text-[#c9cdd4]">{{ data.meta.note }}</p>
  </div>
</template>
