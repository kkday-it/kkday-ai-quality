<script setup lang="ts">
/**
 * 異步區塊統一容器（非表格·非確定進度）：載入中「不確定進度條 + 骨架占位」，三態收斂。
 *
 * 全站分工（見 .claude/rules/frontend-vue.md「異步加載三態一致」）：
 * - 表格異步 → `TableLayout`（內建 loading/error/empty）
 * - 有確定進度的 job（批次初判/導出，知道 processed/total）→ `ExportProgressBar` / `a-progress`
 * - **非表格區塊/詳情/卡片，單請求進度不可知（如訂單佐證）→ 本元件**：頂部不確定進度條
 *   給「正在載入」的實時動態感，下方 `a-skeleton` 撐出內容形狀，避免空白閃爍。
 *
 * 三態互斥：loading > error > empty > 預設插槽（成功內容）。
 */
withDefaults(
  defineProps<{
    loading: boolean;
    /** 錯誤訊息（非空即顯 a-alert）。 */
    error?: string;
    /** 無資料（loading/error 皆否時顯 a-empty）。 */
    empty?: boolean;
    emptyText?: string;
    /** 骨架占位行數。 */
    skeletonRows?: number;
    /** 骨架是否含標題塊（詳情/卡片有標題時開）。 */
    skeletonTitle?: boolean;
  }>(),
  { error: '', empty: false, emptyText: '暫無資料', skeletonRows: 3, skeletonTitle: false },
);
</script>

<template>
  <div>
    <template v-if="loading">
      <!-- 不確定進度條：單請求無百分比可算，用 indeterminate 動畫給實時載入感 -->
      <div class="async-bar" role="progressbar" aria-label="載入中"><span /></div>
      <a-skeleton :animation="true" class="mt-2">
        <a-skeleton-line v-if="skeletonTitle" :rows="1" :widths="['38%']" />
        <a-skeleton-line :rows="skeletonRows" />
      </a-skeleton>
    </template>
    <a-alert v-else-if="error" type="warning">{{ error }}</a-alert>
    <a-empty v-else-if="empty" :description="emptyText" />
    <slot v-else />
  </div>
</template>

<style scoped>
/* 不確定進度條（頂部細條 indeterminate 動畫；Arco 無原生 nprogress，故自繪最小實作） */
.async-bar {
  height: 2px;
  overflow: hidden;
  background: var(--color-fill-2);
  border-radius: 2px;
}
.async-bar span {
  display: block;
  height: 100%;
  width: 40%;
  border-radius: 2px;
  background: rgb(var(--primary-6));
  animation: async-indeterminate 1.1s ease-in-out infinite;
}
@keyframes async-indeterminate {
  0% {
    transform: translateX(-110%);
  }
  100% {
    transform: translateX(320%);
  }
}
</style>
