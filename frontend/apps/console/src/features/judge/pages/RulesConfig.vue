<script setup lang="ts">
import { RulePanels } from '@/features/settings/pages';

// ⚖️ 規則設定（判決邏輯層）：AI 法官專屬配置（歸因分類 / 預判 / 判決規則），故置於 judge 主頁 nav 而非全域「設定」抽屜。
// 內容沿用 RulePanels（資料驅動的 ConfigJsonPanel accordion）；此處僅提供頁面級容器與寬度收斂。
</script>

<template>
  <div class="mx-auto max-w-[960px]">
    <RulePanels />
  </div>
</template>
