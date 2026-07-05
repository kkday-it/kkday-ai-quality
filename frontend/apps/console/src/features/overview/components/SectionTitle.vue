<script setup lang="ts">
/**
 * 分區標題：左側色條 + 標題 + 可選副標，用於切分頁面大區塊（核心指標 / 閉環引擎 / 進線洞察…），
 * 建立一致的視覺節奏，取代散落的 CardSection 標題堆疊。
 */
defineProps<{ title: string; subtitle?: string }>();
</script>

<template>
  <div class="flex items-baseline gap-2.5">
    <span class="h-4 w-1 flex-none rounded-full bg-[#165dff]" />
    <h2 class="text-base font-semibold text-[#1d2129]">{{ title }}</h2>
    <span v-if="subtitle" class="text-xs text-[#86909c]">{{ subtitle }}</span>
  </div>
</template>
