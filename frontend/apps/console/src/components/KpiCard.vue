<script setup lang="ts">
// 跨 feature 公共 KPI 指標卡：label / 數值（可帶單位）/ 說明，點擊冒泡 click。
defineProps<{
  /** 指標名稱 */
  label: string;
  /** 主數值 */
  value: string | number;
  /** 數值後綴單位（如 %），可選 */
  unit?: string;
  /** 卡片底部說明文字 */
  subtext?: string;
}>();
defineEmits<{ (e: 'click'): void }>();
</script>

<template>
  <a-card hoverable class="cursor-pointer" @click="$emit('click')">
    <div class="text-[13px] text-[#86909c]">{{ label }}</div>
    <div class="mt-1 text-2xl font-semibold leading-[1.3]">
      {{ value }}<span v-if="unit" class="ml-0.5 text-sm font-normal">{{ unit }}</span>
    </div>
    <div class="mt-0.5 text-xs text-[#165dff]">{{ subtext }}</div>
  </a-card>
</template>
