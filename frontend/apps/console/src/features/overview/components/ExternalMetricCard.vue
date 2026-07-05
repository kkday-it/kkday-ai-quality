<script setup lang="ts">
// 外部指標卡：指標資料在 Tableau / Mixpanel / Datastudio，我方無原始資料 → 以說明 + 外連呈現。
// h-full 填滿 ChartCard 卡身，與同列圖表卡等高。
defineProps<{ hint?: string; url?: string; dapTable?: string }>();
</script>

<template>
  <div
    class="flex h-full min-h-[160px] flex-col items-start justify-center gap-3 rounded-md border border-dashed border-[var(--color-border-2)] bg-[var(--color-fill-1)] p-5"
  >
    <div class="flex items-center gap-2 text-[#4e5969]">
      <span class="text-lg">🔗</span>
      <span class="text-sm font-medium">外部儀表板指標</span>
    </div>
    <p v-if="hint" class="m-0 text-sm text-[#4e5969]">{{ hint }}</p>
    <p class="m-0 text-xs leading-relaxed text-[#86909c]">
      此指標資料源於外部儀表板，本平台不重複計算<span v-if="dapTable">（{{ dapTable }}）</span>。
    </p>
    <a v-if="url" :href="url" target="_blank" rel="noopener noreferrer" class="mt-1">
      <a-button type="primary" size="small">開啟外部儀表板 ↗</a-button>
    </a>
    <span v-else class="text-xs text-[#c9cdd4]">（連結待補）</span>
  </div>
</template>
