<script setup lang="ts">
import type { AuthUser } from '@/api';
import { MODULES } from '../modules';

// 頂部菜單欄：品牌 + 功能模組下拉 + 設定 / 帳號入口。
// ⚙️ 設定＝公共配置抽屜（LLM/QC）→ open-settings；email chip＝帳號抽屜 → open-account。各自獨立抽屜。
// 功能模組下拉：選項取自 MODULES 註冊表；active 值由殼層依當前路由注入，切換時 emit 由殼層導航。

// topbar 連結（設定 / 帳號）共用樣式
const NAV_LINK =
  'cursor-pointer select-none rounded-md px-3 py-1 text-sm text-[#4e5969] hover:bg-[#e8f3ff] hover:text-[#165dff]';

defineProps<{ user: AuthUser | null; activeModule: string }>();
defineEmits<{
  (e: 'open-settings'): void;
  (e: 'open-account'): void;
  (e: 'switch-module', value: string): void;
}>();
</script>

<template>
  <div class="flex h-[52px] items-center gap-1.5 border-b border-[#f0f0f0] bg-white px-5">
    <span class="select-none text-base font-bold text-[#165dff]">AI 商品質檢</span>
    <span class="text-[#c9cdd4]">/</span>
    <a-select
      :model-value="activeModule"
      class="w-[170px] font-semibold"
      :bordered="false"
      @change="(v) => $emit('switch-module', String(v))"
    >
      <a-option v-for="m in MODULES" :key="m.value" :value="m.value">{{ m.label }}</a-option>
    </a-select>
    <a :class="NAV_LINK" @click="$emit('open-settings')">⚙️ 設定</a>
    <span class="ml-auto flex items-center gap-2">
      <a v-if="user" :class="NAV_LINK" @click="$emit('open-account')">👤 {{ user.email }}</a>
    </span>
  </div>
</template>
