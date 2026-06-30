<script setup lang="ts">
/**
 * 閉環引擎流程圖（步驟卡 + 箭頭 + 回環橫幅）。
 * 取代原 ECharts 環狀 graph：以左到右的編號步驟卡呈現「進線→歸因→Action→審品→供給」，
 * 末端以回環橫幅標示「進線下降回饋源頭」，直觀傳達閉環收斂，無重疊標籤、文字清晰可讀。
 * 步驟頂色依索引取自固定 palette；響應式下自動換行（窄屏改直向堆疊）。
 */
import { IconRight, IconLoop } from '@arco-design/web-vue/es/icon';
import type { LoopStep } from '../types';

defineProps<{ steps: LoopStep[]; caption: string }>();

/** 步驟頂部色條配色（與引擎主色系一致）。 */
const PALETTE = ['#165dff', '#00b42a', '#ff7d00', '#722ed1', '#f53f3f'];
</script>

<template>
  <div class="flex flex-col gap-4">
    <!-- 步驟列：寬屏橫向 + 箭頭，窄屏自動換行 -->
    <div class="flex flex-wrap items-stretch justify-center gap-y-3">
      <template v-for="(s, i) in steps" :key="s.name">
        <div
          class="flex min-w-[150px] max-w-[200px] flex-1 flex-col rounded-lg border border-[#e5e6eb] bg-white p-3 shadow-sm"
          :style="{ borderTopWidth: '3px', borderTopColor: PALETTE[i % PALETTE.length] }"
        >
          <div class="text-sm font-semibold leading-snug text-[#1d2129]">{{ s.name }}</div>
          <div class="mt-1 text-xs leading-relaxed text-[#86909c]">{{ s.sub }}</div>
        </div>
        <!-- 步驟間箭頭（最後一步不加） -->
        <div v-if="i < steps.length - 1" class="flex items-center px-1 text-[#c9cdd4]">
          <icon-right :size="18" />
        </div>
      </template>
    </div>

    <!-- 回環橫幅：標示閉環收斂方向 -->
    <div
      class="flex items-center gap-2 rounded-lg border border-dashed border-[#165dff] bg-[#f3f7ff] px-4 py-2.5 text-[13px] text-[#165dff]"
    >
      <icon-loop :size="16" class="flex-none" />
      <span>{{ caption }}</span>
    </div>
  </div>
</template>
