<script setup lang="ts">
/**
 * 通用圖表卡：所有 view 的渲染單元。依 chartSpec.type 解析 → ECharts <v-chart> 或對應元件。
 * 卡頭：kind 徽章(落後/領先/結構) + 標題 + ⓘhint + 來源連結 + 「放大」(Feature 1，僅 ECharts 圖)。
 * 卡身 flex-1 填滿，配合外層 a-row align=stretch 達成同列等高。data 由父元件依 spec 解析後傳入。
 */
import { computed } from 'vue';
import { useRouter } from 'vue-router';
import VChart from 'vue-echarts';
import NorthStarCard from './NorthStarCard.vue';
import EngineCard from './EngineCard.vue';
import LoopFlow from './LoopFlow.vue';
import SourceTable from './SourceTable.vue';
import ExternalMetricCard from './ExternalMetricCard.vue';
import { buildOption, isEchartsType } from '../registry/chartRegistry';
import type { ChartSpec } from '../dashboard.types';
import type { NorthStarMetric, EngineCard as EngineCardData, LoopStep, SourceRow } from '../types';

const props = defineProps<{ spec: ChartSpec; data?: unknown; caption?: string }>();
const emit = defineEmits<{ (e: 'zoom', spec: ChartSpec): void }>();

const router = useRouter();
const zoomable = computed(() => isEchartsType(props.spec.type));
const option = computed(() => (zoomable.value ? buildOption(props.spec.type, props.data) : null));
const sourceUrl = computed(() => props.spec.source?.dashboardUrl ?? '');

/** kind → 徽章顏色 / 文字（落後 vs 領先 vs 結構）。 */
const KIND_TAG: Record<string, { label: string; color: string }> = {
  lagging: { label: '落後指標', color: 'orange' },
  leading: { label: '領先指標', color: 'green' },
  structural: { label: '結構', color: 'arcoblue' },
};
const kindTag = computed(() => (props.spec.kind ? KIND_TAG[props.spec.kind] : null));

const nsMetrics = computed(() =>
  props.spec.type === 'scorecard' ? ((props.data as NorthStarMetric[]) ?? []) : [],
);
const engineList = computed(() =>
  props.spec.type === 'engines' ? ((props.data as EngineCardData[]) ?? []) : [],
);
const loopSteps = computed(() =>
  props.spec.type === 'loop' ? ((props.data as LoopStep[]) ?? []) : [],
);
const sourceRows = computed(() =>
  props.spec.type === 'table' ? ((props.data as SourceRow[]) ?? []) : [],
);

const chartHeight = computed(() => (props.spec.type === 'gauge' ? 'h-[240px]' : 'h-[300px]'));
const goto = (route: string) => router.push(route);
</script>

<template>
  <a-card
    :bordered="true"
    class="h-full transition-shadow duration-200 hover:shadow-md"
    :body-style="{ padding: '14px 16px', height: '100%', display: 'flex', flexDirection: 'column' }"
  >
    <!-- 卡頭 -->
    <div class="mb-2 flex flex-none items-center gap-2">
      <a-tag v-if="kindTag" :color="kindTag.color" size="small">{{ kindTag.label }}</a-tag>
      <span class="truncate font-medium text-[#1d2129]">{{ spec.title }}</span>
      <a-tooltip v-if="spec.hint" :content="spec.hint">
        <span class="cursor-help text-xs text-[#c9cdd4]">ⓘ</span>
      </a-tooltip>
      <span class="flex-1" />
      <a
        v-if="sourceUrl"
        :href="sourceUrl"
        target="_blank"
        rel="noopener noreferrer"
        class="text-xs text-[#86909c] hover:text-[#165dff]"
      >
        來源 ↗
      </a>
      <a-button v-if="zoomable" size="mini" @click="emit('zoom', spec)">放大</a-button>
    </div>

    <!-- 卡身（flex-1 填滿，配合 align=stretch 等高）-->
    <div class="flex min-h-0 flex-1 flex-col">
      <v-chart
        v-if="zoomable && option"
        :option="option"
        autoresize
        class="w-full"
        :class="chartHeight"
      />

      <div
        v-else-if="spec.type === 'scorecard'"
        class="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4"
      >
        <NorthStarCard v-for="m in nsMetrics" :key="m.key" :metric="m" />
      </div>

      <div v-else-if="spec.type === 'engines'" class="grid grid-cols-1 gap-4 md:grid-cols-3">
        <EngineCard v-for="en in engineList" :key="en.id" :engine="en" @navigate="goto" />
      </div>

      <LoopFlow v-else-if="spec.type === 'loop'" :steps="loopSteps" :caption="caption ?? ''" />

      <SourceTable v-else-if="spec.type === 'table'" :rows="sourceRows" />

      <ExternalMetricCard
        v-else-if="spec.type === 'external'"
        class="flex-1"
        :hint="spec.hint"
        :url="sourceUrl"
        :dap-table="spec.source?.dapTable"
      />
    </div>
  </a-card>
</template>
