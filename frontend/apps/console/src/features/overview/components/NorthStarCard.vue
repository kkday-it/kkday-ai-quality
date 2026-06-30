<script setup lang="ts">
/**
 * 北極星指標卡：大數值 + 環比 delta chip + 目標文字 + ⓘ 說明。
 * tone=core 時加左側強調色條（核心落後指標）；delta 顏色依 deltaGood 語義（占比下降亦可綠）。
 */
import { computed } from 'vue';
import VChart from 'vue-echarts';
import { IconArrowRise, IconArrowFall, IconInfoCircle } from '@arco-design/web-vue/es/icon';
import { buildSparkOption } from '../utils';
import type { NorthStarMetric } from '../types';

const props = defineProps<{ metric: NorthStarMetric }>();

const isCore = computed(() => props.metric.tone === 'core');
/** delta 為正向時綠、負向時紅；方向箭頭依 deltaDir。 */
const deltaClass = computed(() => (props.metric.deltaGood ? 'text-[#00b42a]' : 'text-[#f53f3f]'));
const deltaText = computed(() => `${props.metric.delta > 0 ? '+' : ''}${props.metric.delta}`);
/** sparkline 色：核心指標藍、其餘綠（與 delta 語義一致的正向感）。 */
const sparkColor = computed(() => (isCore.value ? '#165dff' : '#00b42a'));
const sparkOption = computed(() => buildSparkOption(props.metric.spark, sparkColor.value));
</script>

<template>
  <a-card
    class="relative overflow-hidden"
    :class="isCore ? 'border-l-4 border-l-[#165dff]' : ''"
    :body-style="{ padding: '16px 18px' }"
  >
    <div class="flex items-center justify-between">
      <span class="text-[13px] text-[#86909c]">{{ metric.label }}</span>
      <a-popover :trigger="['hover', 'click']" position="br">
        <icon-info-circle class="cursor-pointer text-[#c9cdd4] hover:text-[#165dff]" />
        <template #content>
          <div class="max-w-[220px] text-xs leading-relaxed text-gray-600">{{ metric.hint }}</div>
        </template>
      </a-popover>
    </div>

    <div class="mt-2 flex items-end gap-2">
      <span class="text-[28px] font-semibold leading-none text-[#1d2129]">{{ metric.value }}</span>
      <span class="pb-0.5 text-sm text-[#4e5969]">{{ metric.unit }}</span>
      <span class="ml-auto flex items-center gap-0.5 text-sm font-medium" :class="deltaClass">
        <component :is="metric.deltaDir === 'up' ? IconArrowRise : IconArrowFall" />
        {{ deltaText }}
      </span>
    </div>

    <div class="mt-2 flex items-end justify-between gap-2">
      <span class="text-xs" :class="isCore ? 'font-medium text-[#165dff]' : 'text-[#86909c]'">
        {{ metric.targetText }}
      </span>
      <v-chart :option="sparkOption" autoresize class="h-8 w-20 flex-none" />
    </div>
  </a-card>
</template>
