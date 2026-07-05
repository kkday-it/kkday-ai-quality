<script setup lang="ts">
/**
 * config 驅動的業務目標儀表板 view（內容質量&閉環引擎 / 售前轉化 / 售後履約 共用一頁）。
 * 由路由末段決定 viewKey → 讀 dashboard.json 的 views[viewKey] → 逐 section 渲 SectionTitle + 等高 ChartCard grid。
 * 結構/公共/特有皆由 config 組合；改 dashboard.json 即改版面。
 */
import { computed, ref } from 'vue';
import { useRoute } from 'vue-router';
import { ChartCard, ChartModal, SectionTitle } from '../components';
import { resolveChartData } from '../utils';
import dashboard from '@config/overview/dashboard.json';
import mock3 from '../mock/overview.mock3.json';
import type { ChartSpec, DashboardConfig, Overview3, SectionSpec, ViewSpec } from '../dashboard.types';

const config = dashboard as unknown as DashboardConfig;
const data = mock3 as unknown as Overview3;

const route = useRoute();
const viewKey = computed(() => {
  const seg = route.path.split('/').pop() ?? 'content';
  return config.views[seg] ? seg : 'content';
});
const view = computed<ViewSpec>(() => config.views[viewKey.value]);
const sections = computed<SectionSpec[]>(() => view.value.sections ?? []);

const specsOf = (section: SectionSpec): ChartSpec[] =>
  section.charts
    .map((id) => {
      const spec = config.charts[id];
      // dashboard.json 為業務可編輯 config；chart id 打錯會靜默缺圖，warn 出來便於定位 typo（非拿掉）
      if (!spec) console.warn(`[overview] dashboard.json 區塊「${section.title}」引用了不存在的 chart id：${id}`);
      return spec;
    })
    .filter(Boolean);
const resolveData = (spec: ChartSpec): unknown => resolveChartData(spec, data);

// 單圖放大（Feature 1）
const modalOpen = ref(false);
const zoomSpec = ref<ChartSpec | null>(null);
const zoomData = ref<unknown>(null);
const onZoom = (spec: ChartSpec) => {
  zoomSpec.value = spec;
  zoomData.value = resolveData(spec);
  modalOpen.value = true;
};
</script>

<template>
  <div class="mx-auto max-w-[1320px]">
    <header class="mb-5 flex flex-wrap items-end justify-between gap-2">
      <div>
        <h1 class="m-0 text-xl font-semibold text-[#1d2129]">{{ view.label }}</h1>
        <p class="mt-1 text-sm text-[#86909c]">{{ data.meta.subtitle }} · {{ data.meta.period }}</p>
      </div>
      <a-tag color="orange" size="small" bordered>Demo · mock 資料</a-tag>
    </header>

    <section v-for="(sec, i) in sections" :key="i" class="mb-6">
      <SectionTitle :title="sec.title" :subtitle="sec.desc" />
      <a-row :gutter="[16, 16]" align="stretch" class="mt-3">
        <a-col v-for="spec in specsOf(sec)" :key="spec.id" :span="spec.grid ?? 24">
          <ChartCard
            :spec="spec"
            :data="resolveData(spec)"
            :caption="data.meta.loopCaption"
            @zoom="onZoom"
          />
        </a-col>
      </a-row>
    </section>

    <p class="mt-2 text-center text-xs text-[#c9cdd4]">{{ data.meta.note }}</p>

    <ChartModal v-model:visible="modalOpen" :spec="zoomSpec" :data="zoomData" />
  </div>
</template>
