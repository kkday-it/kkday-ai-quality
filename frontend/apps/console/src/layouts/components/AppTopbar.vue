<script setup lang="ts">
import { MODULES } from '../modules';

// 頂部菜單欄：品牌 + 功能模組下拉（左）+ 配置入口（右）。
// ⚙️ 配置＝公共設定抽屜（LLM/QC/導出偏好）→ open-settings。
// 功能模組下拉：選項取自 MODULES 註冊表；active 值由殼層依當前路由注入，切換時 emit 由殼層導航。

// topbar 連結（配置）共用樣式
const NAV_LINK =
  'cursor-pointer select-none rounded-md px-3 py-1 text-sm text-[var(--color-text-2)] hover:bg-[var(--color-primary-light-1)] hover:text-[rgb(var(--primary-6))]';

defineProps<{ activeModule: string }>();
defineEmits<{
  (e: 'open-settings'): void;
  (e: 'switch-module', value: string): void;
}>();
</script>

<template>
  <div class="flex h-[52px] items-center gap-1.5 border-b border-[var(--color-border)] bg-white px-5">
    <span class="select-none text-base font-bold text-[rgb(var(--primary-6))]">AI 質檢</span>
    <span class="text-[var(--color-text-4)]">/</span>
    <a-select
      :model-value="activeModule"
      class="w-[170px] font-semibold"
      :bordered="false"
      @change="(v) => $emit('switch-module', String(v))"
    >
      <a-option v-for="m in MODULES" :key="m.value" :value="m.value">{{ m.label }}</a-option>
    </a-select>
    <!-- 配置入口置於右側 -->
    <span class="ml-auto flex items-center gap-2">
      <a :class="NAV_LINK" @click="$emit('open-settings')">⚙️ 配置</a>
    </span>
  </div>
</template>
