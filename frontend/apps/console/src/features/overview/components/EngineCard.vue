<script setup lang="ts">
/**
 * 三大引擎卡：P級 / 狀態 tag + 標語 + 達成判定 + 指標列 + 迷你趨勢 + CTA。
 * route 存在 → CTA 為可點按鈕（emit navigate）；否則顯示為禁用狀態文字（規劃中 / H2 上線）。
 */
import { computed } from 'vue';
import VChart from 'vue-echarts';
import { IconRight } from '@arco-design/web-vue/es/icon';
import { buildSparkOption } from '../utils';
import type { EngineCard } from '../types';

const props = defineProps<{ engine: EngineCard }>();
const emit = defineEmits<{ (e: 'navigate', route: string): void }>();

/** P 級對應色：P0 紅、P1 橙、其餘灰。 */
const pColor = computed(() => ({ P0: 'red', P1: 'orange' })[props.engine.pLevel] ?? 'gray');
const sparkColor = computed(() => ({ ai_review: '#165dff', ai_writing: '#00b42a', ai_judge: '#ff7d00' })[props.engine.id] ?? '#165dff');
const sparkOption = computed(() => buildSparkOption(props.engine.spark, sparkColor.value));
</script>

<template>
  <a-card
    class="h-full transition-shadow hover:shadow-md"
    :class="engine.route ? 'cursor-pointer' : ''"
    :body-style="{ padding: '18px' }"
    @click="engine.route && emit('navigate', engine.route)"
  >
    <div class="flex items-center gap-2">
      <a-tag :color="pColor" size="small" class="font-semibold">{{ engine.pLevel }}</a-tag>
      <span class="text-base font-semibold text-[#1d2129]">{{ engine.name }}</span>
      <a-tag :color="engine.statusColor" size="small" class="ml-auto">{{ engine.status }}</a-tag>
    </div>
    <div class="mt-1 text-xs text-[#86909c]">{{ engine.tagline }}</div>
    <div class="mt-2 text-[13px] leading-relaxed text-[#4e5969]">{{ engine.goal }}</div>

    <!-- 指標列 + 迷你趨勢 -->
    <div class="mt-3 flex items-end justify-between gap-3">
      <div class="flex flex-wrap gap-x-5 gap-y-2">
        <div v-for="m in engine.metrics" :key="m.label">
          <div class="text-[11px] text-[#86909c]">{{ m.label }}</div>
          <div class="text-lg font-semibold leading-tight text-[#1d2129]">
            {{ m.value }}<span class="ml-0.5 text-xs font-normal text-[#86909c]">{{ m.unit }}</span>
          </div>
        </div>
      </div>
      <v-chart :option="sparkOption" autoresize class="h-12 w-24 flex-none" />
    </div>

    <!-- CTA -->
    <div class="mt-3 border-t border-[#f2f3f5] pt-2.5">
      <a-link v-if="engine.route" :hoverable="false" class="text-sm">
        {{ engine.cta }}<icon-right class="ml-0.5" />
      </a-link>
      <span v-else class="text-xs text-[#c9cdd4]">{{ engine.cta }}</span>
    </div>
  </a-card>
</template>
